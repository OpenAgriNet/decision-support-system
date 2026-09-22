"""Contract tests for the span a /discover call leaves behind.

Discovery never raises. An exception here would cancel every sibling query in
the caller's task group, so failures come back as data inside a
`DiscoveryResult` — which means a span around this call succeeds by default. A
provider that timed out would leave a green span indistinguishable from one
that answered. The span has to read the result and mark itself.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.core.provider_discovery.models import ProviderQuery

QUERY = ProviderQuery(
    capabilities=("openagrinet:MandiPrice",),
    subject_category="Market",
    languages=("hi",),
    coverage=None,
)


class _FakeSchemaPackCache:
    """Returns what `SchemaContextSource` declares — a `str` per capability,
    the pack's own `@context` URL. A double that returns some other shape
    leaves the seam untested, which is the whole point of a tier-2 test."""

    def current_schema_context(self) -> dict[str, str]:
        return {"openagrinet:MandiPrice": "https://packs.example/MandiPrice/v0.1"}


class _EmptySchemaPackCache:
    """A capability the capability index knows and this one does not — what a
    skipped pack leaves behind."""

    def current_schema_context(self) -> dict[str, str]:
        return {}


@pytest.fixture
def spans(monkeypatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    return exporter


def _discovery(handler) -> HttpCapabilityDiscovery:
    return HttpCapabilityDiscovery(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://discovery-network-vistaar.da.gov.in/oan",
        schema_pack_cache=_FakeSchemaPackCache(),
    )


async def test_both_log_lines_for_one_call_carry_the_same_span(spans, caplog) -> None:
    """`span_id` is what joins a log line to a span without a search, and the
    request line is the one naming what was sent. If the two halves of a call
    report different spans, clicking the failed span and grepping its id finds
    the outcome but not the request that caused it."""

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "simulated"})

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        await _discovery(unavailable).discover(QUERY, (0,), "txn-1")

    lines = [ln for ln in caplog.text.splitlines() if "external=discovery" in ln]
    span_ids = {
        field.removeprefix("span_id=")
        for line in lines
        for field in line.split()
        if field.startswith("span_id=")
    }

    assert [ln for ln in lines if "event=request" in ln]
    assert [ln for ln in lines if "event=response" in ln]
    # One id, and a real one — two lines both reporting `-` would agree while
    # joining nothing.
    assert len(span_ids) == 1
    assert span_ids != {"-"}
    (span,) = spans.get_finished_spans()
    assert span_ids == {format(span.context.span_id, "016x")}


async def test_a_capability_with_no_schema_context_does_not_raise(spans) -> None:
    """`discover` must not raise, and building the request is part of it.

    `_schema_context_urls` subscripts the schema-context index unguarded, and
    the two indices are separate cache reads with no cross-check — a pack
    skipped as malformed leaves a capability in one and not the other. Raising
    here would escape into the caller's task group and cancel every sibling
    query, which is the disaster the whole no-raise rule exists to prevent.
    """

    def never_called(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the request was never built, so nothing should be sent")

    discovery = HttpCapabilityDiscovery(
        client=httpx.AsyncClient(transport=httpx.MockTransport(never_called)),
        base_url="https://discovery-network-vistaar.da.gov.in/oan",
        schema_pack_cache=_EmptySchemaPackCache(),
    )

    result = await discovery.discover(QUERY, (0,), "txn-1")

    assert result.failures[0]
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR


async def test_a_returned_failure_marks_the_span_failed(spans) -> None:
    """The case an ordinary span gets wrong. Nothing was raised, so without
    reading the result this span reports success on a query that found
    nobody."""

    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "simulated"})

    result = await _discovery(unavailable).discover(QUERY, (0,), "txn-1")

    assert result.failures[0]  # the call did fail, as data
    (span,) = spans.get_finished_spans()
    assert span.name == "dss.discover"
    assert span.status.status_code is StatusCode.ERROR
