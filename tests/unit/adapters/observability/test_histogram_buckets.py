"""Tier 1 — a deployed histogram keeps our bucket edges.

Logfire's default views turn every histogram into an exponential one. A view
beats an instrument's bucket hint, so without this our seconds and USD edges
applied only in tests. The dashboard reads explicit buckets, so those panels
would be empty too.
"""

from __future__ import annotations

from opentelemetry.sdk.metrics.export import HistogramDataPoint, InMemoryMetricReader

from dss.adapters.observability.metrics import record_turn
from dss.adapters.observability.tracing import configure_telemetry


def test_histograms_use_explicit_buckets_under_logfire(monkeypatch) -> None:
    import logfire

    reader = InMemoryMetricReader()
    real_configure = logfire.configure

    def configure_and_read(**kwargs):
        metrics = kwargs.get("metrics") or logfire.MetricsOptions()
        metrics.additional_readers = [*metrics.additional_readers, reader]
        real_configure(**{**kwargs, "metrics": metrics})

    # Same setup as `test_session_stamping`: real logfire, nothing exported.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    for signal in ("TRACES", "METRICS", "LOGS"):
        monkeypatch.setenv(f"OTEL_{signal}_EXPORTER", "none")
    monkeypatch.setattr("logfire.configure", configure_and_read)
    monkeypatch.setattr("pydantic_ai.agent.Agent.instrument_all", lambda *_a: None)

    configure_telemetry()
    record_turn(status="answered", elapsed_ms=2500.0, cost=0.004)

    points = [
        point
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "dss.turn.duration"
        for point in metric.data.data_points
    ]
    assert points, "dss.turn.duration was not published"
    assert all(isinstance(point, HistogramDataPoint) for point in points)
    assert points[0].explicit_bounds[-1] >= 60
