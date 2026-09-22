"""Contract tests for the span a /discover call leaves behind.

Discovery never raises. An exception here would cancel every sibling query in
the caller's task group, so failures come back as data inside a
`DiscoveryResult` — which means a span around this call succeeds by default. A
provider that timed out would leave a green span indistinguishable from one
that answered. The span has to read the result and mark itself.
"""

from __future__ import annotations

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
    def current_schema_context(self) -> dict[str, tuple[str, str]]:
        return {"openagrinet:MandiPrice": ("MandiPrice", "v0.1")}


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
