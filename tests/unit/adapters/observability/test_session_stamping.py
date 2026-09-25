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
    apart — the same rule the stage-span slot follows.

    The real `logfire.configure` runs. Its provider is not an SDK
    `TracerProvider`, and a stand-in that was one hid that the processor was
    never added."""

    import logfire

    exporter = InMemorySpanExporter()
    real_configure = logfire.configure

    def configure_and_capture(**kwargs):
        extra = kwargs.pop("additional_span_processors", None) or []
        real_configure(
            **kwargs,
            additional_span_processors=[*extra, SimpleSpanProcessor(exporter)],
        )

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    # The endpoint turns our tracing on, but logfire also reads it and adds its
    # own exporter, which retries against a port nobody listens on. `none`
    # keeps logfire from exporting; the span still reaches `exporter`.
    for signal in ("TRACES", "METRICS", "LOGS"):
        monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "none")
    monkeypatch.setattr("logfire.configure", configure_and_capture)
    monkeypatch.setattr("pydantic_ai.agent.Agent.instrument_all", lambda *_a: None)

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


def test_a_span_carries_the_session_under_the_sdks_key_too(spans) -> None:
    """Langfuse's own SDK writes the session as `session.id`. On a v4
    deployment the Sessions page did not list a session sent only as
    `langfuse.session.id`, though the observation filter found it."""

    bind_turn_ids("txn-1", message_id="msg-1", session_id="my-session-42")

    with trace.get_tracer("t").start_as_current_span("planner run"):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes.get("session.id") == "my-session-42"
