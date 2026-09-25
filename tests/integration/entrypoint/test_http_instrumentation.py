"""Tier 2 — what the HTTP instrumentor may and may not put in telemetry.

It is there for request metrics. Its spans added about a hundred ASGI
send/receive spans per turn, sat above `dss.turn` as the trace root, and
carried the raw query string and full exception messages. Spans say what
failed by type, never by message, so it gets no spans at all.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.entrypoint.app import _instrument_http

SECRET = "my phone is 98450"


@pytest.fixture
def spans(monkeypatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    yield exporter


def _app(meter_provider: MeterProvider | None = None) -> FastAPI:
    app = FastAPI()

    @app.get("/ok")
    def ok() -> dict:
        with trace.get_tracer("t").start_as_current_span("dss.turn"):
            return {"ok": True}

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError(SECRET)

    _instrument_http(app, meter_provider=meter_provider)
    return app


def test_a_request_opens_no_http_spans_and_the_turn_stays_the_root(spans) -> None:
    TestClient(_app()).get("/ok")

    finished = spans.get_finished_spans()
    assert [span.name for span in finished] == ["dss.turn"]
    assert finished[0].parent is None


def test_no_query_string_or_exception_message_reaches_a_span(spans) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)

    client.get("/ok", params={"q": SECRET})
    client.get("/boom")

    for span in spans.get_finished_spans():
        assert SECRET not in str(dict(span.attributes))
        assert SECRET not in str(span.status.description)
        for event in span.events:
            assert SECRET not in str(dict(event.attributes))


def test_request_metrics_are_still_published(spans) -> None:
    reader = InMemoryMetricReader()
    TestClient(_app(MeterProvider(metric_readers=[reader]))).get("/ok")

    names = {
        metric.name
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert "http.server.request.duration" in names
