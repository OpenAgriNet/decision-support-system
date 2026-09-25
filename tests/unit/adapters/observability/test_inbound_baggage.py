"""Tier 1 — a caller's baggage never becomes a span attribute.

Logfire copies OpenTelemetry Baggage onto every span by default. The HTTP
instrumentor puts a caller's `baggage` header into the request's context, so a
caller could set `langfuse.user.id`, `langfuse.session.id` or a trace name on
our spans. Those are ours to set.
"""

from __future__ import annotations

from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.observability.tracing import configure_telemetry


def test_inbound_baggage_is_not_copied_onto_spans(monkeypatch) -> None:
    import logfire

    exporter = InMemorySpanExporter()
    real_configure = logfire.configure

    def configure_and_capture(**kwargs):
        extra = kwargs.pop("additional_span_processors", None) or []
        real_configure(
            **kwargs,
            additional_span_processors=[*extra, SimpleSpanProcessor(exporter)],
        )

    # Same setup as `test_session_stamping`: real logfire, nothing exported.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    for signal in ("TRACES", "METRICS", "LOGS"):
        monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "none")
    monkeypatch.setattr("logfire.configure", configure_and_capture)
    monkeypatch.setattr("pydantic_ai.agent.Agent.instrument_all", lambda *_a: None)

    configure_telemetry()

    token = context.attach(baggage.set_baggage("langfuse.user.id", "spoofed"))
    try:
        with trace.get_tracer("t").start_as_current_span("dss.turn"):
            pass
    finally:
        context.detach(token)

    (span,) = exporter.get_finished_spans()
    assert "langfuse.user.id" not in span.attributes
    assert "spoofed" not in str(dict(span.attributes))
