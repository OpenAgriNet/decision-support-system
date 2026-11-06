"""Trace logging: stage and agent entry/exit, and calls to outside services.

- One place, so every line has the same fields: `component`, `agent` or
  `external`, then `event`. Fields ride on the record, never in the text, so
  HyperDX can filter on them (see `log_fields`).
- The turn's ids are added by `log_fields.TurnIdsFilter`. A caller passes
  `transaction_id` only when it holds a different one.
- Bodies hold the farmer's words, so they go to DEBUG, which is never exported.
- Everything goes to `dss.trace`; tune that one logger to change verbosity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any, Protocol

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


# Bodies over this are clipped so one line stays readable; the clip length is
# generous because the whole point here is to see what came back.
_MAX_BODY = 8000


@contextmanager
def trace_component(
    component: Stage | str, transaction_id: str | None = None
) -> Iterator[None]:
    """Log `enter` and `exit` around a stage, open its span, and publish its time.

    A stage that raises still logs `exit` and its duration, with the error type
    as its status, then re-raises unchanged.
    """

    stage = Stage(component)
    opener = _stage_span_opener
    span_context = nullcontext() if opener is None else opener(f"dss.stage.{stage}")

    start = time.monotonic()
    _info(f"{stage} enter", transaction_id, component=stage.value, event="enter")
    status = "ok"
    try:
        with span_context:
            yield
    except BaseException as exc:  # re-raised; we only record how it ended
        status = _error_status(exc)
        raise
    finally:
        elapsed_ms = _elapsed_ms_since(start)
        _log(
            _level_for(status),
            f"{stage} exit",
            transaction_id,
            component=stage.value,
            event="exit",
            status=status,
            elapsed_ms=elapsed_ms,
        )
        recorder = _stage_metric_recorder
        if recorder is not None:
            # Read here: the model that answered is known only once it has.
            recorder(stage=stage.value, elapsed_ms=elapsed_ms, model=model_for(stage))


class TokenUsage(Protocol):
    """A run's token counter, filled in while the run goes."""

    input_tokens: int
    output_tokens: int


@contextmanager
def trace_agent_call(agent: str, *, model: str, usage: TokenUsage) -> Iterator[None]:
    """Log one model call before it starts and after it ends, however it ends.

    Never the prompt or the answer: those are the farmer's words.
    """

    start = time.monotonic()
    _info(f"{agent} call", agent=agent, event="agent_call", model=model)
    status = "ok"
    try:
        yield
    except BaseException as exc:  # re-raised; we only record how it ended
        status = _error_status(exc)
        raise
    finally:
        _log(
            _level_for(status),
            f"{agent} done",
            agent=agent,
            event="agent_done",
            model=model,
            status=status,
            elapsed_ms=_elapsed_ms_since(start),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )


def log_event(
    component: str,
    transaction_id: str | None = None,
    *,
    event: str,
    **fields: Any,
) -> None:
    """Log one thing a component decided. INFO only, so never the farmer's words."""

    _info(
        f"{component} {event}",
        transaction_id,
        component=component,
        event=event,
        **fields,
    )


def log_failure(
    component: str,
    transaction_id: str | None = None,
    *,
    event: str,
    error_type: str,
    **fields: Any,
) -> None:
    """Log a failure the DSS caught and answered around, at WARNING.

    The error's type, never its message by default: a message can quote the
    farmer. Pass a concealed one as a field when it helps.
    """

    _log(
        logging.WARNING,
        f"{component} {event}",
        transaction_id,
        component=component,
        event=event,
        error_type=error_type,
        **fields,
    )


def log_external_request(
    service: str,
    transaction_id: str | None = None,
    *,
    body: Any = None,
    **fields: Any,
) -> None:
    """Log one request on its way out to an outside service; its body at DEBUG."""

    _info(
        f"{service} request",
        transaction_id,
        external=service,
        event="request",
        **fields,
    )
    _debug_body(service, transaction_id, event="request_body", body=body)


def log_external_response(
    service: str,
    transaction_id: str | None = None,
    *,
    status: Any = None,
    body: Any = None,
    **fields: Any,
) -> None:
    """Log one response from an outside service; its body at DEBUG.

    `status` is an HTTP code or a short tag such as `transport_error`.
    """

    if status is not None:
        fields["status"] = status
    _log(
        _level_for(status),
        f"{service} response",
        transaction_id,
        external=service,
        event="response",
        **fields,
    )
    _debug_body(service, transaction_id, event="response_body", body=body)


def _info(message: str, transaction_id: str | None = None, **fields: Any) -> None:
    _log(logging.INFO, message, transaction_id, **fields)


def _log(
    level: int, message: str, transaction_id: str | None = None, **fields: Any
) -> None:
    logger.log(level, message, extra=_with_transaction_id(transaction_id, fields))


def _level_for(status: Any) -> int:
    """WARNING for a line that reports a failure, so HyperDX can filter on level.

    A failure is an HTTP error code or any tag but `ok` and `cancelled`.
    """

    is_failure = (
        status >= 400
        if isinstance(status, int)
        else status not in (None, "ok", _CANCELLED)
    )
    return logging.WARNING if is_failure else logging.INFO


def _debug_body(
    service: str, transaction_id: str | None, *, event: str, body: Any
) -> None:
    if body is None:
        return
    fields = {"external": service, "event": event, "body": _as_text(body)}
    logger.debug(
        f"{service} {event}", extra=_with_transaction_id(transaction_id, fields)
    )


def _with_transaction_id(
    transaction_id: str | None, fields: dict[str, Any]
) -> dict[str, Any]:
    """A passed id wins over the turn's; `None` leaves the turn's to the filter."""

    if transaction_id is None:
        return fields
    return {**fields, "transaction_id": transaction_id}


# A farmer who closed the screen, or a stream closed early: not a failure.
_CANCELLED = "cancelled"


def _error_status(exc: BaseException) -> str:
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return _CANCELLED
    return f"error:{type(exc).__name__}"


def _elapsed_ms_since(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)


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
