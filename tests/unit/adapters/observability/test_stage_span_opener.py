"""Tier 1 — the opener that fills `trace_component`'s slot.

`trace_log.py` holds a slot and imports no telemetry SDK. This is what goes in
it: the one function that knows OpenTelemetry, living where vendor code is
allowed to live.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dss.adapters.observability import tracing
from dss.adapters.observability.tracing import configure_tracing, open_span
from dss.observability import trace_log


@pytest.fixture(autouse=True)
def _restore_module_globals():
    """Put both module globals back after every test here.

    `configure_tracing` writes two of them, and `monkeypatch` cannot undo
    either: `set_stage_span_opener` rebinds through `global`, and
    `set_model_names` mutates the dict in place rather than replacing it. A
    test that leaves the opener installed makes every later test in the session
    open real spans, which is order-dependent and invisible until something
    counts spans.
    """

    before_opener = trace_log._stage_span_opener
    before_names = dict(tracing._model_names)
    yield
    trace_log.set_stage_span_opener(before_opener)
    tracing.set_model_names(**before_names)


def _exporting(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    return exporter


def test_an_exception_message_never_reaches_a_span(monkeypatch) -> None:
    """A span says *that* something failed and what type it was, never what it
    said. Exception text is not safe by default here: `SelectFailed` embeds the
    provider's whole response body, which echoes the farmer's query — and
    `DSS_ARCHITECTURE.md` §6.1 puts traces on the list of places that may not
    carry personal data.

    Unbounded, too. The log path clips a body at 8000 characters; an exception
    message has no such limit, so an HTML error page would go to the exporter
    whole.
    """

    exporter = _exporting(monkeypatch)
    secret = "phone=9876543210 query='tomato price near me'"

    with pytest.raises(RuntimeError):
        with open_span("dss.select"):
            raise RuntimeError(f"select failed: 400 ({secret})")

    (span,) = exporter.get_finished_spans()
    recorded = str(span.status.description) + str(
        [dict(event.attributes or {}) for event in span.events]
    )
    assert secret not in recorded
    assert "RuntimeError" in str(span.status.description)


def test_a_cancelled_span_is_marked_failed(monkeypatch) -> None:
    """Cancellation is how a turn ends when the farmer closes the screen
    mid-answer, so it is a case worth seeing. OpenTelemetry ignores it — it
    records only what derives from `Exception`, and `CancelledError` derives
    straight from `BaseException` — which would leave every abandoned turn
    looking exactly like a completed one."""

    import asyncio

    exporter = _exporting(monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        with open_span("dss.stage.composer"):
            raise asyncio.CancelledError

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "CancelledError"


def test_it_opens_a_span_under_the_current_one(monkeypatch) -> None:
    """Nesting is the point. A stage span that parented to nothing would show
    up as its own trace, which is the grouping this story exists to add."""

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)

    with provider.get_tracer("test").start_as_current_span("dss.turn"):
        with open_span("dss.stage.intent"):
            pass

    stage, turn = exporter.get_finished_spans()
    assert stage.name == "dss.stage.intent"
    assert stage.parent.span_id == turn.context.span_id


def test_configuring_tracing_fills_the_slot(monkeypatch) -> None:
    """The slot is filled by whatever turns tracing on, so the two cannot
    drift apart: an endpoint configured but no stage spans appearing would be
    a silent hole exactly where this story adds its value."""

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr("logfire.configure", lambda **_kwargs: None)
    monkeypatch.setattr("pydantic_ai.agent.Agent.instrument_all", lambda *_a: None)
    monkeypatch.setattr(trace_log, "_stage_span_opener", None)

    configure_tracing()

    assert trace_log._stage_span_opener is open_span


def test_the_model_names_arrive_with_everything_else(monkeypatch) -> None:
    """One call configures the whole module. A second entry point for the
    model names would let a caller turn tracing on and leave them behind, with
    nothing to say so."""

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr("logfire.configure", lambda **_kwargs: None)
    monkeypatch.setattr("pydantic_ai.agent.Agent.instrument_all", lambda *_a: None)
    monkeypatch.setattr(tracing, "_model_names", {})

    configure_tracing(intent_model="openai:gpt-4o-mini")

    assert tracing._model_names == {"intent_model": "openai:gpt-4o-mini"}


def test_without_an_endpoint_the_slot_stays_empty(monkeypatch) -> None:
    """The quiet default. No endpoint means no exporter, so a span would go
    nowhere — and `trace_component` is on the path of every stage of every
    turn, where "goes nowhere" should still mean "does nothing"."""

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(trace_log, "_stage_span_opener", None)
    monkeypatch.setattr(tracing, "_model_names", {})

    configure_tracing(intent_model="openai:gpt-4o-mini")

    # Nothing is recording, so there is no span for a model name to sit on.
    assert tracing._model_names == {}

    assert trace_log._stage_span_opener is None
