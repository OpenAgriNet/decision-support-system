"""Tier 1 — `trace_component` opens a span, through a slot filled at startup.

The timing these lines already log is the timing wanted on the trace. What is
missing is the grouping: a log line cannot be nested under a turn, compared
across runs, or graphed. A span can.

`trace_log.py` imports nothing from `adapters/` — the span opener arrives at
startup instead, through `set_stage_span_opener`. An unfilled slot means no
span, which is exactly right when no OTLP endpoint is configured: every test
and every local run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dss.adapters.observability.tracing import open_span
from dss.observability.trace_log import set_stage_span_opener, trace_component


@pytest.fixture
def spans(monkeypatch) -> Iterator[InMemorySpanExporter]:
    """Fill the slot with the real opener, writing to an in-memory exporter.

    `open_span` itself, not a lambda that merely resembles it. A stand-in here
    would let the two drift — and they did: `open_span` suppresses exception
    messages for PII, so a `start_as_current_span` lambda would keep passing a
    test asserting behaviour production no longer has.

    Its own `TracerProvider` rather than the global one: `set_tracer_provider`
    is one-shot per process, so a global would make this order-dependent and
    leak into every other test. The slot is emptied again after, for the same
    reason — an opener left behind would span every later test's stages.
    """

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)

    set_stage_span_opener(open_span)
    yield exporter
    set_stage_span_opener(None)


def test_a_component_opens_a_span_named_for_its_stage(spans) -> None:
    with trace_component("intent", "txn-1"):
        pass

    assert [span.name for span in spans.get_finished_spans()] == ["dss.stage.intent"]


def test_a_stage_that_raises_leaves_a_failed_span(spans) -> None:
    """A stage that blew up must not look like one that succeeded. The type
    goes on the span, so the reader knows what kind of failure it was without
    the message, which may carry the farmer's words."""

    with pytest.raises(RuntimeError, match="provider unreachable"):
        with trace_component("discovery", "txn-1"):
            raise RuntimeError("provider unreachable")

    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "RuntimeError"
    assert "provider unreachable" not in str(span.status.description)


def test_an_unfilled_slot_opens_no_span(monkeypatch) -> None:
    """No OTLP endpoint means the slot is never filled, and a stage must then
    open nothing at all. This is every test and every local run, so it is the
    path that has to stay quiet.

    Watched at the tracer rather than at an exporter: with no opener there is
    nothing to export *to*, so "no spans collected" would pass even if the
    module had quietly reached for the global tracer instead."""

    tracers_asked_for: list[str] = []
    monkeypatch.setattr("dss.observability.trace_log._stage_span_opener", None)
    monkeypatch.setattr(trace, "get_tracer", tracers_asked_for.append)

    with trace_component("intent", "txn-1"):
        pass

    assert tracers_asked_for == []
