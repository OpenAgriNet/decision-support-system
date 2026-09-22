"""Tier 1 — the opener that fills `trace_component`'s slot.

`trace_log.py` holds a slot and imports no telemetry SDK. This is what goes in
it: the one function that knows OpenTelemetry, living where vendor code is
allowed to live.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.observability import tracing
from dss.adapters.observability.tracing import configure_tracing, open_span
from dss.observability import trace_log


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
