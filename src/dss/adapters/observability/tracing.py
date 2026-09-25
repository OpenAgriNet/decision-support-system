"""Agent runs as OpenTelemetry spans.

`Agent.instrument_all()` is the whole integration: one call and every Pydantic
AI agent's runs become spans — which agent ran, how long it took, how many
tokens, which tool it called, where it failed. The DSS builds agents in three
places and none of them changes.

What a span must *not* carry is message content. Pydantic AI includes it by
default — `gen_ai.input.messages` holds the farmer's query verbatim,
`pydantic_ai.all_messages` holds the whole conversation — and
`DSS_ARCHITECTURE.md` §6.1 forbids precisely that: "prompts containing
personal data" and "traces" are both named. So `include_content` is off unless
someone asks for it by name, which is a thing to do on a laptop and not in a
deployment.

Traces go wherever `OTEL_EXPORTER_OTLP_ENDPOINT` points — a local Collector,
Langfuse's OTLP endpoint, anything. Unset means tracing is off, which is the
quiet default for a local run and every test. Reading the standard variable
rather than a `DSS_`-prefixed one keeps the destination a deployment concern.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry.sdk.trace import SpanProcessor

from dss.adapters.observability.metrics import (
    configure_metrics,
    record_composed,
    record_first_delta,
    record_turn,
    reset_metrics,
)
from dss.observability.trace_log import current_session_id, set_stage_span_opener
from dss.observability.turn_usage import begin_turn_usage, current_turn_usage

if TYPE_CHECKING:
    from opentelemetry.trace import Span

logger = logging.getLogger(__name__)

_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_INCLUDE_CONTENT = "DSS_TRACE_INCLUDE_MESSAGE_CONTENT"


def tracing_enabled() -> bool:
    """Whether an OTLP destination is configured."""

    return bool(os.environ.get(_ENDPOINT))


def instrumentation_settings(*, tracer_provider=None):
    """How much of each agent run to record.

    `include_content=False` strips every message body while keeping roles,
    part types, token counts and latency — so a span still says what ran and
    how it went, without saying what was asked.

    Only a literal `"true"` opts in. `1`, `yes` and a stray space all read as
    off: a permissive parser here would mean a typo in a deployment's
    environment sending a farmer's words out of the process.

    `tracer_provider` is for a test that needs to read the spans back. Left
    unset, Pydantic AI uses the global one.
    """

    from pydantic_ai.models.instrumented import InstrumentationSettings

    include = os.environ.get(_INCLUDE_CONTENT, "") == "true"
    return InstrumentationSettings(
        include_content=include, tracer_provider=tracer_provider
    )


class TurnIdSpanProcessor(SpanProcessor):
    """Puts the turn's session id on every span.

    Langfuse says an attribute it filters on "needs to be present on each span
    in the trace, not only on the root span". We set it on `dss.turn` only, so
    the agent runs inside a turn showed a different session.

    The id comes from the context variable `bind_turn_ids` sets. Anyio copies
    the context into child tasks, so every branch of a turn sees the same one.
    Outside a turn there is none, and nothing is stamped.

    Langfuse suggests OpenTelemetry Baggage instead. It needs a new dependency,
    and what it adds — passing the id between processes — a single-process turn
    does not need.
    """

    def on_start(self, span, parent_context=None) -> None:  # noqa: ANN001
        session_id = current_session_id()
        if session_id is not None:
            span.set_attribute("langfuse.session.id", session_id)


def _metric_views():
    """Logfire's default metric views, minus the one that makes every
    histogram exponential.

    A view beats an instrument's bucket hint, so with it our seconds and USD
    edges applied only in tests, and the dashboard's explicit-bucket queries
    found nothing. The other two defaults are restated here rather than
    filtered out of logfire's list, which would mean reading its private
    fields: drop the SDK's own metrics, and bound the active-requests labels.
    """

    from opentelemetry.sdk.metrics import UpDownCounter
    from opentelemetry.sdk.metrics.view import DropAggregation, View

    return [
        View(instrument_name="otel.sdk.*", aggregation=DropAggregation()),
        View(
            instrument_type=UpDownCounter,
            instrument_name="http.server.active_requests",
            attribute_keys={
                "url.scheme",
                "http.scheme",
                "http.flavor",
                "http.method",
                "http.request.method",
            },
        ),
    ]


def configure_telemetry(
    *,
    intent_model: str | None = None,
    moderation_model: str | None = None,
    planner_model: str | None = None,
    composer_model: str | None = None,
    model_profile: str = "default",
) -> None:
    """Send agent runs and turn metrics to the configured OTLP endpoint, if
    there is one.

    Called once from `create_app`. Absent an endpoint this does nothing at all
    but put the module back as it was — no exporter, no instrumentation, no
    warning, because a local run having none is normal rather than a
    misconfiguration. `create_app` runs more than once (tests, `--reload`), so
    the reset matters: leaving a previous boot's opener installed would keep
    stage spans on while the exporter is off.

    The model names go on every turn span, and arrive here rather than through
    a second call so tracing cannot be on with them left behind. Named one by
    one rather than `**kwargs`: they are a closed set of four, and a
    transposition should stop the process rather than quietly produce an
    attribute nobody filters on. `model_profile` joins them for the same
    reason — it names the whole model configuration on every turn metric, and
    nothing derives it.

    Metrics and traces share this one call and this one endpoint, so metrics
    cannot be on with tracing off — a state nothing needs. Whether a metric
    then leaves the process is a second, separate gate: `OTEL_METRICS_EXPORTER`
    ships as `none`, because the usual destination is Langfuse and Langfuse
    discards metrics.
    """

    if not tracing_enabled():
        set_stage_span_opener(None)
        set_model_names()
        reset_metrics()
        return

    set_model_names(
        **{
            name: value
            for name, value in (
                ("intent_model", intent_model),
                ("moderation_model", moderation_model),
                ("planner_model", planner_model),
                ("composer_model", composer_model),
            )
            if value is not None
        }
    )

    endpoint = os.environ[_ENDPOINT]
    if not endpoint.startswith(("http://", "https://")):
        # Without a scheme the exporter fails once per batch, forever, with
        # `No connection adapters were found` — a line about the URL rather
        # than about the missing `http://`, buried in the export loop rather
        # than raised at startup. Say it once, at the top, in those terms.
        logger.error(
            "%s is %r, which has no scheme. Exports will fail with 'No "
            "connection adapters were found'. Prefix it with http:// or "
            "https:// — and note the OTLP/HTTP port is 4318, not 4317.",
            _ENDPOINT,
            endpoint,
        )

    settings = instrumentation_settings()
    if settings.include_content:
        logger.warning(
            "%s is on: spans will carry the farmer's query and the composed "
            "answer verbatim. DSS_ARCHITECTURE.md §6.1 does not permit that "
            "in a deployment — use it locally and turn it off.",
            _INCLUDE_CONTENT,
        )

    import logfire
    from pydantic_ai.agent import Agent

    # `send_to_logfire=False`: logfire is used for its OTEL instrumentation of
    # Pydantic AI, not as a destination. Where traces go is the OTLP endpoint's
    # business, and shipping them to a third-party SaaS is not a decision this
    # module should make silently.
    # `scrubbing=False`: logfire's default scrubber redacts any attribute whose
    # key matches a sensitive-name pattern, and `langfuse.session.id` matches on
    # "session" — the id arrived at Langfuse as the literal string
    # "[Scrubbed due to 'session']", so filtering by session found nothing.
    #
    # Turning it off is consistent with the rest of this deployment rather than
    # a new exposure: `include_content` already puts the query and the answer in
    # the span, so a scrubber over the id attributes was protecting nothing that
    # was not already there.
    #
    # The session processor goes in through logfire, not onto the global
    # provider afterwards. Logfire's provider is its own proxy, not an SDK
    # `TracerProvider`, so a processor added from outside never runs — and
    # every agent span then fell back to Pydantic AI's per-run
    # `gen_ai.conversation.id`, which Langfuse also reads as a session.
    #
    # `add_baggage_to_attributes=False`: logfire copies OpenTelemetry Baggage
    # onto every span by default, and a caller's `baggage` header reaches the
    # request context. A caller could then set `langfuse.user.id`, the session
    # or the trace name on our spans. Those are ours to set.
    logfire.configure(
        send_to_logfire=False,
        console=False,
        scrubbing=False,
        add_baggage_to_attributes=False,
        metrics=logfire.MetricsOptions(views=_metric_views()),
        additional_span_processors=[TurnIdSpanProcessor()],
    )
    Agent.instrument_all(settings)

    # Here rather than in `create_app` so stage spans cannot be on while the
    # exporter is off, or the reverse.
    #
    # The slot covers the stage spans only. The adapters call `open_span`
    # directly, because `trace_log.py` may not import this module and they have
    # no such constraint. With no endpoint configured those still reach a
    # no-op tracer — cheap, and it exports nothing — whereas the stage path
    # runs six times per turn and is worth skipping outright.
    set_stage_span_opener(open_span)

    # Fills the second slot too, so a stage cannot be spanned but unmeasured.
    configure_metrics(model_profile=model_profile)

    logger.info("telemetry on, exporting to %s", endpoint)


@contextmanager
def open_span(
    name: str, attributes: dict[str, str | int | float | bool] | None = None
) -> Iterator[Span]:
    """A named span, nested under whatever is current.

    Callers: it fills `trace_component`'s slot, so every stage gets a
    `dss.stage.<name>` span; `orchestration/` opens one around the discovery
    fan-out; and the two network adapters open one per outbound call.

    It lives here because this package is where telemetry SDK code is allowed
    to live. `observability/trace_log.py` imports none of it — it holds a slot,
    and this arrives at startup instead.

    Naming is the caller's, so each span name sits beside the thing it is named
    for rather than in a table here.

    The span is yielded because not every caller can leave it to say what
    happened: the discovery client never raises — its failures come back as
    data. Callers with nothing to add can ignore what is yielded.

    **A failure is recorded by type, never by message.** OpenTelemetry's own
    handling would put `exception.message` and a full `exception.stacktrace` on
    the span, and an exception message here is not safe to export: `SelectFailed`
    embeds the provider's entire response body, which echoes the farmer's query
    back. `DSS_ARCHITECTURE.md` §6.1 names traces as a place personal data may
    not reach, and unlike the log path there is no length clip — an HTML error
    page would go whole to the exporter.

    **`BaseException` too, not just `Exception`.** OpenTelemetry deliberately
    ignores anything deriving straight from `BaseException`, so a cancelled turn
    would close green. Cancellation is how a turn ends when the farmer closes
    the screen mid-answer, which is exactly a case worth seeing.
    """

    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    tracer = trace.get_tracer("dss.orchestration")
    with tracer.start_as_current_span(
        name,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except BaseException as exc:
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


# Which model answered is the first thing asked of a turn that answered badly,
# and the four are separately configurable (ADR-0004). Held here rather than
# passed per turn: they are the same on every turn of a process, so threading
# them through the orchestrator would carry constants down four layers.
_model_names: dict[str, str] = {}


def set_model_names(**names: str) -> None:
    _model_names.clear()
    _model_names.update(names)


def set_current_span_attributes(**attributes: str | int | float | bool) -> None:
    """Add facts to whichever span is open, without opening one.

    For a caller whose work is already bracketed by a span one frame up. A span
    of its own would start and end with that one — the empty nesting ADR-0012
    rejects — while the facts still belong on the trace.

    A no-op when nothing is recording, like every other call in this module.
    """

    from opentelemetry import trace

    trace.get_current_span().set_attributes(dict(attributes))


class TurnRecorder:
    """What a turn can add to its own root span while it is still running.

    `status` and the timings are not known when the span opens, and the
    timings have to be taken at the instant they happen rather than measured
    afterwards. So the orchestrator holds this and calls a verb per thing.

    Each timing also records a span event, so the moment shows on the trace
    timeline as well as reading as a number.

    Deliberately not a setter per attribute: an absent timing must stay absent
    rather than arrive as zero, and a method that is simply never called is
    harder to get wrong than a default.
    """

    def __init__(self, span, started: float) -> None:  # noqa: ANN001
        self._span = span
        self._started = started
        # Unset until the turn names its outcome. A turn that crashes or is
        # abandoned never does, and is stamped `error` on the way out.
        self._status: str | None = None

    def _elapsed_ms(self) -> float:
        return (time.monotonic() - self._started) * 1000

    def first_delta(self) -> None:
        """The farmer's first word of the answer."""

        record_first_delta(self._mark("first_delta_ms"))

    def composed(self) -> None:
        """The answer is fully written and its sources are attached.

        Not a mid-stream moment: sources are resolved from the whole text, so
        this cannot happen until the last delta has arrived. Read it
        against `first_delta_ms` — that one is how long the farmer waited to
        see anything, and the gap between them is how long the writing took.
        """

        record_composed(self._mark("composed_ms"))

    def _mark(self, attribute: str) -> float:
        """Stamp the moment on the span and return it, so the metric records
        the same reading rather than a second one."""

        elapsed_ms = self._elapsed_ms()
        self._span.set_attribute(attribute, elapsed_ms)
        self._span.add_event(attribute, {"elapsed_ms": elapsed_ms})
        return elapsed_ms

    def status(self, status: str) -> None:
        """How the turn ended — one of the contract's statuses, or ``error``
        for a turn that crashed or was abandoned before it could name one.

        Always set, so a breakdown by status accounts for every turn.
        """

        self._status = status
        self._span.set_attribute("status", status)

    def _publish(self) -> None:
        """Count this turn and publish its duration and cost.

        Called once, from `turn_span`'s exit rather than from the orchestrator's
        `_finish`. `_finish` is only reached on the four normal exits, and a
        turn that crashed or was abandoned has to be counted too — that is the
        turn a graph most needs to show.

        A turn with no named outcome is stamped ``error`` here. One that named
        its outcome keeps it, even if the caller then closes it rather than
        reading to the end — that is a hang-up after the answer, not a crash.
        """

        if self._status is None:
            self.status("error")
        usage = current_turn_usage()
        record_turn(
            status=self._status,
            elapsed_ms=self._elapsed_ms(),
            cost=usage.cost if usage is not None else 0.0,
        )


@contextmanager
def turn_span(
    *, trace_id: str, message_id: str, session_id: str
) -> Iterator[TurnRecorder]:
    """The span every one of a turn's other spans hangs off.

    Pydantic AI opens its own spans inside `Agent.run()` and the DSS does not
    own them, so the turn's ids cannot be set on them directly. They go on a
    parent span opened around the whole turn instead, which Langfuse reads as
    the trace root — and a root's attributes are what its trace is filed under.

    `langfuse.session.id` is the one Langfuse promotes to a first-class
    Session, so `sessionId` filters and groups in the UI. `transactionId` and
    `messageId` have no native field and go to trace metadata: the same string
    you grep for in the log, found through the metadata filter rather than the
    id box.

    Absent an OTLP endpoint there is no real tracer provider, so this is a
    no-op that costs a function call — which is every test and every local run
    without tracing configured.

    Lives here rather than in `orchestration/` so that package imports no
    telemetry SDK. When this grows past one function it should become a proper
    port under `ports/`, with this as its OpenTelemetry adapter.

    Yields a `TurnRecorder`, because the turn's own facts — its outcome, and
    when the farmer first heard anything — are known only while it runs.

    A turn that crashes or is abandoned never reaches the code that names its
    outcome, so `status` is stamped ``error`` on exit instead. A turn that did
    name it keeps it, even if closed early after its terminal event. Leaving it off
    would drop exactly those turns out of any breakdown by status — the one
    place they most need to appear.
    """

    with open_span(
        "dss.turn",
        attributes={
            "langfuse.session.id": session_id,
            "langfuse.trace.metadata.transaction_id": trace_id,
            "langfuse.trace.metadata.message_id": message_id,
            **_model_names,
        },
    ) as span:
        begin_turn_usage()
        recorder = TurnRecorder(span, time.monotonic())
        try:
            yield recorder
        finally:
            recorder._publish()
