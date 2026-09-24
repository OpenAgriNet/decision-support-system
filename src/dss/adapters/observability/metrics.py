"""A turn's numbers, published where a dashboard can graph them.

Spans answer "why was *this* turn slow". They are the wrong tool for "is this
deployment slower than last week" — answering that from raw spans means
aggregating thousands of them every time a graph refreshes. Metrics are the
other half: pre-aggregated, cheap to graph, and what a release comparison
actually subtracts.

**No MeterProvider is built here.** `logfire.configure()` already builds one
from the environment, exactly as it builds the tracer provider — which is why
no `TracerProvider` appears in `tracing.py` either. A second one would mean two
configuration paths for one endpoint. What decides whether anything leaves the
process is `OTEL_METRICS_EXPORTER`: it ships as `none`, because the default
destination is Langfuse and Langfuse discards metrics. A deployment with a
collector in front sets it to `otlp`.

**Labels are a contract, and a bounded one.** Every distinct combination of
label values becomes its own time series. Six stages, a handful of models, six
statuses and one profile per deployment stay small. Adding an unbounded label
later — a provider id, a district, a farmer id — would multiply series until
the backend degrades, and that failure shows up in the backend months later
rather than in a build. `LABEL_KEYS` is what a test holds us to.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dss.observability.stages import Stage
from dss.observability.trace_log import set_stage_metric_recorder
from dss.observability.turn_usage import add_cost, record_stage_model

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, MeterProvider

logger = logging.getLogger(__name__)

TURN_DURATION = "dss.turn.duration"
TURN_FIRST_DELTA = "dss.turn.first_delta.duration"
TURN_FIRST_CLAIM = "dss.turn.first_claim.duration"
TURN_COUNT = "dss.turn.count"
TURN_COST = "dss.turn.cost"
STAGE_DURATION = "dss.stage.duration"
STAGE_TOKENS = "dss.stage.tokens"

# Every label every instrument is allowed to carry. Read by the guard test, and
# the place to look before adding one. `model` is absent from the stage
# instruments' values for enrichment and discovery, which run no model — an
# absent label reads as "not applicable", where a "none" value would read as a
# model name in a dashboard's dropdown.
LABEL_KEYS: dict[str, frozenset[str]] = {
    TURN_DURATION: frozenset({"status", "model_profile"}),
    TURN_FIRST_DELTA: frozenset({"model_profile"}),
    TURN_FIRST_CLAIM: frozenset({"model_profile"}),
    STAGE_DURATION: frozenset({"stage", "model"}),
    TURN_COUNT: frozenset({"status", "model_profile"}),
    STAGE_TOKENS: frozenset({"stage", "model", "direction"}),
    TURN_COST: frozenset({"model_profile"}),
}

# Seconds, not milliseconds, although the spans carry `*_ms`. The same
# dashboard shows `http.server.request.duration`, which the OpenTelemetry HTTP
# convention fixes at seconds, and a panel mixing the two units is a bug nobody
# sees until they read the axis.
_SECONDS = "s"


class _Instruments:
    """The seven instruments, built once per configured meter."""

    def __init__(self, meter) -> None:  # noqa: ANN001
        self.turn_duration: Histogram = meter.create_histogram(
            TURN_DURATION,
            unit=_SECONDS,
            description="Wall-clock time of a whole turn, however it ended.",
        )
        self.turn_first_delta: Histogram = meter.create_histogram(
            TURN_FIRST_DELTA,
            unit=_SECONDS,
            description=(
                "Time to the first word of the answer. This is the wait the "
                "farmer actually feels."
            ),
        )
        self.turn_first_claim: Histogram = meter.create_histogram(
            TURN_FIRST_CLAIM,
            unit=_SECONDS,
            description=(
                "Time to the end of composition. The first claim needs the "
                "whole text for its sources, so it lands after the last word."
            ),
        )
        self.stage_duration: Histogram = meter.create_histogram(
            STAGE_DURATION,
            unit=_SECONDS,
            description="Wall-clock time of one stage of a turn.",
        )
        self.turn_count: Counter = meter.create_counter(
            TURN_COUNT,
            description="Turns, by how they ended. Request rate derives from this.",
        )
        self.stage_tokens: Counter = meter.create_counter(
            STAGE_TOKENS,
            unit="{token}",
            description=(
                "Tokens per stage, split by direction. Output tokens are "
                "generated one at a time and drive latency; input tokens are "
                "processed together and mostly drive cost. One combined count "
                "hides which of them grew."
            ),
        )
        self.turn_cost: Histogram = meter.create_histogram(
            TURN_COST,
            unit="USD",
            description=(
                "Cost of a turn, where the model has a published price. A "
                "self-hosted model has none, so this reads zero and the token "
                "counts are the number that matters instead."
            ),
        )


_instruments: _Instruments | None = None
_model_profile = "default"


def configure_metrics(
    *, model_profile: str, meter_provider: MeterProvider | None = None
) -> None:
    """Build the instruments and let `trace_component` publish stage duration.

    Called from `configure_telemetry`, so metrics cannot be on with tracing
    off. `meter_provider` is for a test that needs to read the measurements
    back; left unset, the global one logfire configured is used.
    """

    global _instruments, _model_profile

    from opentelemetry import metrics as otel_metrics

    provider = meter_provider or otel_metrics.get_meter_provider()
    _model_profile = model_profile
    _instruments = _Instruments(provider.get_meter("dss"))
    set_stage_metric_recorder(record_stage_duration)


def reset_metrics() -> None:
    """Take the instruments away again.

    `create_app` runs more than once in a process (tests, `--reload`), and a
    boot with no OTLP endpoint must leave nothing behind from the last one.
    """

    global _instruments
    _instruments = None
    set_stage_metric_recorder(None)


def _turn_labels(**extra: str) -> dict[str, str]:
    return {"model_profile": _model_profile, **extra}


def record_stage_duration(*, stage: str, elapsed_ms: float, model: str | None) -> None:
    """One stage finished. Called through the slot, from `trace_component`."""

    if _instruments is None:
        return
    labels = {"stage": stage}
    if model is not None:
        labels["model"] = model
    _instruments.stage_duration.record(elapsed_ms / 1000, labels)


def record_stage_tokens(
    *, stage: Stage, model: str, input_tokens: int, output_tokens: int
) -> None:
    """One model call's tokens. Called where the call returns."""

    if _instruments is None:
        return
    _instruments.stage_tokens.add(
        input_tokens, {"stage": stage.value, "model": model, "direction": "input"}
    )
    _instruments.stage_tokens.add(
        output_tokens, {"stage": stage.value, "model": model, "direction": "output"}
    )


def record_agent_run(*, stage: Stage, result) -> None:  # noqa: ANN001
    """Publish one model call's tokens and cost, and name the model that ran.

    Takes the run result rather than the three numbers: every call site would
    otherwise unpack it the same way, and the unpacking is the part that moves
    when Pydantic AI changes. Duck-typed on purpose — nothing here imports the
    framework.

    Pydantic AI has counted all of this on its own spans since tracing was
    wired; this does not re-count it, it aggregates it into metrics a dashboard
    can graph without reading a thousand traces.

    The model name is the one that *answered*, read off the response, not the
    string someone put in config. An `azure:` binding names a deployment, and
    which model sits behind a deployment can change without the config moving.

    `cost` is `None` until Pydantic AI knows the model's price, so a
    self-hosted deployment reports zero. Expected, not a bug — tokens are the
    number that matters there.
    """

    usage = result.usage
    model = result.response.model_name or "unknown"
    record_stage_model(stage, model)
    record_stage_tokens(
        stage=stage,
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )
    add_cost(float(usage.cost or 0))


def record_first_delta(elapsed_ms: float) -> None:
    """The farmer's first word of the answer — time to first word."""

    if _instruments is None:
        return
    _instruments.turn_first_delta.record(elapsed_ms / 1000, _turn_labels())


def record_first_claim(elapsed_ms: float) -> None:
    """The first complete claim, with its sources — end of composition.

    Not time to first word: sources come from the whole text, so this lands
    after the last delta. `record_first_delta` is the first-word wait.
    """

    if _instruments is None:
        return
    _instruments.turn_first_claim.record(elapsed_ms / 1000, _turn_labels())


def record_turn(*, status: str, elapsed_ms: float, cost: float) -> None:
    """A turn ended — answered, refused, crashed or abandoned.

    One call for all three turn instruments, from one place, so a turn cannot
    be counted without its duration or the other way round.
    """

    if _instruments is None:
        return
    labels = _turn_labels(status=status)
    _instruments.turn_duration.record(elapsed_ms / 1000, labels)
    _instruments.turn_count.add(1, labels)
    _instruments.turn_cost.record(cost, _turn_labels())
