"""Tier 1 — every span in a turn carries the turn's session id.

Langfuse says an attribute it filters on "needs to be present on each span in
the trace, not only on the root span". With it set on `dss.turn` alone, the
agent runs inside a turn showed a different session.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.observability.tracing import TurnIdSpanProcessor, configure_tracing
from dss.observability.trace_log import bind_turn_ids


@pytest.fixture
def spans(monkeypatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(TurnIdSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    yield exporter


def test_configuring_tracing_registers_the_processor(monkeypatch) -> None:
    """The processor only works if it is on the provider every span goes
    through. Registered by whatever turns tracing on, so the two cannot drift
    apart — the same rule the stage-span slot follows."""

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr("logfire.configure", lambda **_kwargs: None)
    monkeypatch.setattr("pydantic_ai.agent.Agent.instrument_all", lambda *_a: None)
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)

    configure_tracing()

    # Asserted through a span rather than by reading the provider's processor
    # list, which is private to the SDK.
    bind_turn_ids("txn-1", message_id="msg-1", session_id="my-session-42")
    with trace.get_tracer("t").start_as_current_span("anything"):
        pass

    (span,) = exporter.get_finished_spans()
    assert span.attributes["langfuse.session.id"] == "my-session-42"


def test_a_nested_span_carries_the_turns_session(spans) -> None:
    """The case that was broken. An agent run nests under `dss.turn`, and
    needs the session on itself rather than only above it."""

    bind_turn_ids("txn-1", message_id="msg-1", session_id="my-session-42")
    tracer = trace.get_tracer("t")

    with tracer.start_as_current_span("dss.turn"):
        with tracer.start_as_current_span("planner run"):
            pass

    stamped = {
        span.name: span.attributes.get("langfuse.session.id")
        for span in spans.get_finished_spans()
    }
    assert stamped == {
        "planner run": "my-session-42",
        "dss.turn": "my-session-42",
    }
