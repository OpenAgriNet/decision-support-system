"""Trace logging: component entry/exit and external-service responses.

One place so the format is uniform. Each line is a short message, like
``discovery exit``, and its fields ride on the record as attributes. A log
search such as HyperDX filters on them, and the console prints them as
``key=value`` (`log_format.py`). Every line carries a ``request_id`` — the
turn's ``transactionId``, which is also its ``traceId`` — so a whole turn's path
joins on one key.

Three things get logged:

- **Component span** — ``trace_component(name)`` logs ``event=enter`` on the way
  in and ``event=exit`` on the way out, with ``elapsed_ms`` and whether it left
  by returning or raising. It also opens a ``dss.stage.<name>`` span, so the
  same timing is groupable and comparable rather than only greppable. Wrap the
  call at its orchestration seam, so ``core/`` services stay logging-free and
  pure.
- **External request** — ``log_external_request(service, ...)`` logs a call on
  its way out, at the ``/discover`` and ``/select`` call sites. Its ``body``
  goes to DEBUG rather than INFO: a request body carries the farmer's query
  verbatim, and §6.2's sink-level redaction interceptor is not built yet.
- **External response** — ``log_external_response(service, ...)`` logs what a
  provider or model handed back. The user asked for *every* external response,
  so this is called at each LLM and network call site.

Request and response share ``service`` and ``request_id`` and differ only by
``event=``, so one call's two halves read in order under one grep.

Everything goes to the ``dss.trace`` logger at INFO; raise or lower that one
logger to tune verbosity without touching the rest of the app. Bodies can be
large and may contain farmer-facing content — see CONVENTIONS.md on PII before
shipping these lines to a broad-retention sink.

``request_id`` need not be passed everywhere: it is stashed in a context
variable at the top of the turn (``bind_turn_ids``), so an adapter with no
``transaction_id`` in hand (the LLM provider port takes none) still tags its
line correctly. Anyio copies the context into each child task, so the id set
before the intent/moderation task group reaches both branches.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, Protocol

from opentelemetry import trace

from dss.observability.stages import Stage
from dss.observability.turn_usage import model_for

logger = logging.getLogger("dss.trace")


class StageSpanOpener(Protocol):
    """Opens a span for one stage, given its full name.

    A span *opener*, not a generic wrapper: `trace_component` builds the
    `dss.stage.<component>` name itself, so the naming rule lives in one line
    next to the component name it is built from.
    """

    def __call__(self, name: str) -> AbstractContextManager[Any]: ...


# A slot rather than an import: this module is the inward-facing one and may
# not depend on `adapters/`, so `configure_telemetry()` hands it an opener at
# startup instead. Unfilled means no span, which is the right behaviour with
# no OTLP endpoint configured — every test and every local run.
_stage_span_opener: StageSpanOpener | None = None


def set_stage_span_opener(opener: StageSpanOpener | None) -> None:
    """Fill (or empty) the slot `trace_component` opens its span through."""

    global _stage_span_opener
    _stage_span_opener = opener


class StageMetricRecorder(Protocol):
    """Publishes one stage's elapsed time as a metric.

    Takes the elapsed time rather than measuring it: `trace_component` already
    measures it for the log line, and two clocks over the same block would
    eventually disagree.
    """

    def __call__(self, *, stage: str, elapsed_ms: float, model: str | None) -> None: ...


# The second slot, for the same reason as the first: this module may not import
# `adapters/`, so `configure_telemetry()` hands it a recorder at startup.
# Unfilled means no metric — every test, and every local run with no exporter.
_stage_metric_recorder: StageMetricRecorder | None = None


def set_stage_metric_recorder(recorder: StageMetricRecorder | None) -> None:
    """Fill (or empty) the slot `trace_component` publishes its timing through."""

    global _stage_metric_recorder
    _stage_metric_recorder = recorder


_request_id: ContextVar[str] = ContextVar("dss_request_id", default="-")
_message_id: ContextVar[str] = ContextVar("dss_message_id", default="-")
_session_id: ContextVar[str] = ContextVar("dss_session_id", default="-")

# What an id renders as when there isn't one. `-` rather than an empty value or
# OpenTelemetry's all-zeros id: a zeroed trace id looks like a real one in a
# grep until you stop and count the characters.
_ABSENT = "-"

# Bodies over this are clipped so one line stays readable; the clip length is
# generous because the whole point here is to see what came back.
_MAX_BODY = 8000


def bind_turn_ids(
    request_id: str,
    *,
    message_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Make this turn's ids ambient for everything downstream in its context
    (and its child tasks). Called once at the top of a turn.

    ``request_id`` is the turn's ``transactionId``. ``message_id`` and
    ``session_id`` are the other two the caller filters on; they are keyword
    arguments and default to absent because only the orchestrator holds a
    ``TurnContext`` carrying all three.
    """

    _request_id.set(request_id)
    if message_id is not None:
        _message_id.set(message_id)
    if session_id is not None:
        _session_id.set(session_id)


def current_request_id() -> str:
    return _request_id.get()


def current_session_id() -> str | None:
    """This turn's ``sessionId``, or ``None`` outside a turn.

    ``None`` rather than the ``-`` the log lines render: the caller decides
    whether to set a span attribute at all, and one reading ``-`` is worse
    than none.
    """

    session_id = _session_id.get()
    return None if session_id == _ABSENT else session_id


def _rid(request_id: str | None) -> str:
    return request_id if request_id is not None else _request_id.get()


def current_otel_ids() -> tuple[str, str]:
    """The active span's trace and span ids, as the hex Langfuse displays.

    Read from the *current* span, which makes `span_id` mean different things
    on different lines. A line written inside a Pydantic AI tool carries that
    tool's span — `external=invocation` from the planner's `select` tool
    reports the `select` span, and clicking it in Langfuse lands on the call.
    A `trace_component` line carries the span its stage runs *inside* rather
    than the stage's own: both lines are written outside the stage span, so
    `enter` and `exit` agree with each other.

    `trace_id` is exact on every line either way.

    Both are ``-`` when nothing is recording: no OTLP endpoint, so no exporter,
    so no span context. That is every test and every local run.
    """

    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return _ABSENT, _ABSENT
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def _turn_ids(request_id: str | None) -> dict[str, str]:
    """The turn's ids, minus any that are absent.

    No trace or span id: the log bridge stamps both on the record from the
    active span, and the console formatter prints them.
    """

    ids = {
        "request_id": _rid(request_id),
        "message_id": _message_id.get(),
        "session_id": _session_id.get(),
    }
    return {key: value for key, value in ids.items() if value != _ABSENT}


def _log(level: int, message: str, **fields: Any) -> None:
    logger.log(
        level,
        message,
        extra={key: _as_attribute(value) for key, value in fields.items()},
    )


def _as_attribute(value: Any) -> str | int | float | bool:
    """An OpenTelemetry attribute takes only primitives; anything else is dropped."""

    return value if isinstance(value, (str, int, float, bool)) else str(value)


@contextmanager
def trace_component(
    component: Stage | str, request_id: str | None = None
) -> Iterator[None]:
    """Log ``enter`` before the wrapped work and ``exit`` after — with the
    elapsed time and whether it returned or raised — open a
    ``dss.stage.<component>`` span around it, and publish the same elapsed time
    as a metric. Wrap an ``await`` directly:

        with trace_component(Stage.INTENT, turn.transaction_id):
            intent = await classify_intent(turn, llm)

    Both the span and the metric go through slots, so with neither registered
    this costs two ``None`` checks and the two log lines it always wrote. A
    stage that raises leaves the span ``ERROR`` with the exception recorded on
    it, then re-raises unchanged — and still publishes its duration, because a
    stage that failed slowly is exactly what a graph needs to show.
    """

    stage = Stage(component)
    opener = _stage_span_opener
    span_context = nullcontext() if opener is None else opener(f"dss.stage.{stage}")

    start = time.monotonic()
    _log(
        logging.INFO,
        f"{stage} enter",
        **_turn_ids(request_id),
        component=stage.value,
        event="enter",
    )
    status = "ok"
    try:
        with span_context:
            yield
    except BaseException as exc:  # re-raised; we only tag the span
        status = f"error:{type(exc).__name__}"
        raise
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        _log(
            logging.INFO,
            f"{stage} exit",
            **_turn_ids(request_id),
            component=stage.value,
            event="exit",
            status=status,
            elapsed_ms=round(elapsed_ms, 1),
        )
        recorder = _stage_metric_recorder
        if recorder is not None:
            # Read here rather than at entry: the model that answered is only
            # known once the call has returned.
            recorder(stage=stage.value, elapsed_ms=elapsed_ms, model=model_for(stage))


def log_event(
    component: str,
    request_id: str | None = None,
    *,
    event: str,
    **fields: Any,
) -> None:
    """Log one notable thing a component decided, on the same greppable key.

    `enter`/`exit` say a component ran; this says what it did. Nothing here
    goes to DEBUG, so pass only fields safe at INFO — config and catalog
    values, never the farmer's words (CONVENTIONS.md).
    """

    _log(
        logging.INFO,
        f"{component} {event}",
        **_turn_ids(request_id),
        component=component,
        event=event,
        **fields,
    )


def log_external_request(
    service: str,
    request_id: str | None = None,
    *,
    body: Any = None,
    **fields: Any,
) -> None:
    """Log one request on its way out to an external service.

    The mirror of `log_external_response`: same ``service`` names, same
    ``request_id``, and ``event=request`` against its ``event=response`` — so
    one turn's outbound and inbound lines join on one key and read in call
    order.

    The body is logged at DEBUG while the rest of the line is INFO. A request
    body carries the farmer's query verbatim, and the redaction interceptor
    §6.2 puts at the sink does not exist yet — so the shape of a call is
    always visible and its words are opt-in, by lowering the ``dss.trace``
    logger.
    """

    _log_external(service, request_id, event="request", body=body, **fields)


def log_external_response(
    service: str,
    request_id: str | None = None,
    *,
    status: Any = None,
    body: Any = None,
    **fields: Any,
) -> None:
    """Log one response from an external service (a model or a network call).

    ``service`` names it (``llm``, ``llm.planner``, ``discovery``,
    ``invocation``); ``status`` is an HTTP code or a short tag; ``body`` is the
    payload, rendered to text and clipped. Extra ``**fields`` become attributes
    for anything worth pinning next to it (schema, capability).
    """

    if status is not None:
        fields["status"] = status
    _log_external(service, request_id, event="response", body=body, **fields)


def _log_external(
    service: str,
    request_id: str | None,
    *,
    event: str,
    body: Any,
    **fields: Any,
) -> None:
    """The INFO line, then the body on its own DEBUG line so it never exports."""

    ids = _turn_ids(request_id)
    _log(
        logging.INFO,
        f"{service} {event}",
        **ids,
        external=service,
        event=event,
        **fields,
    )
    if body is not None:
        _log(
            logging.DEBUG,
            f"{service} {event}_body",
            **ids,
            external=service,
            event=f"{event}_body",
            body=_as_text(body),
        )


def _as_text(body: Any) -> str:
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(body, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(body)
    else:
        text = str(body)
    if len(text) > _MAX_BODY:
        dropped = len(text) - _MAX_BODY
        return f"{text[:_MAX_BODY]}...<+{dropped} chars clipped>"
    return text
