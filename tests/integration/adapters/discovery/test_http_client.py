"""Contract tests for HttpCapabilityDiscovery — the real network hop.

Uses httpx2.MockTransport so no real socket is ever opened, per tier 2's
'recorded fixture or local test server, never live network calls' rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.core.provider_discovery.models import ProviderQuery

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://discovery-network-vistaar.da.gov.in/oan"


class _FakeSchemaPackCache:
    """Stands in for SchemaPackCache — this adapter reads it live, not a
    snapshot, so a refresh() elsewhere is reflected without re-wiring.
    """

    def __init__(self, schema_context_index: dict[str, tuple[str, str]]) -> None:
        self._schema_context_index = schema_context_index

    def current_schema_context(self) -> dict[str, tuple[str, str]]:
        return self._schema_context_index


def _client_returning(
    response_body: dict, status_code: int = 200
) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, json=response_body)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _discovery(
    client: httpx2.AsyncClient, schema_pack_cache=None
) -> HttpCapabilityDiscovery:
    if schema_pack_cache is None:
        schema_pack_cache = _FakeSchemaPackCache(
            {"openagrinet:WeatherObservation": ("WeatherObservation", "v0.1")}
        )
    return HttpCapabilityDiscovery(
        client=client,
        base_url=BASE_URL,
        schema_pack_cache=schema_pack_cache,
        schema_base_url="https://schemas.openagrinet.global/schema",
    )


async def test_discover_posts_to_the_discover_endpoint_and_maps_the_response() -> None:
    on_discover = json.loads((FIXTURES / "on_discover_response.json").read_text())
    discovery = _discovery(_client_returning(on_discover))
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    result = await discovery.discover(query, ask_indices=(0,))

    assert result.capabilities[0][0].provider_id == "mausamgram"


async def test_a_refreshed_cache_is_reflected_without_rewiring() -> None:
    """Proves the adapter reads the cache live, not a construction-time
    snapshot — the schema_context_index dict is mutated after wiring.
    """
    on_discover = json.loads((FIXTURES / "on_discover_response.json").read_text())
    index: dict[str, tuple[str, str]] = {}
    cache = _FakeSchemaPackCache(index)
    discovery = _discovery(_client_returning(on_discover), schema_pack_cache=cache)
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    index["openagrinet:WeatherObservation"] = ("WeatherObservation", "v0.1")
    result = await discovery.discover(query, ask_indices=(0,))

    assert result.capabilities[0][0].provider_id == "mausamgram"


async def test_a_non_2xx_response_raises() -> None:
    """Classifying transient vs defect is discover_providers' job — this
    adapter only needs to surface the failure, not decide what it means.
    """
    discovery = _discovery(
        _client_returning({"error": "rate limited"}, status_code=429)
    )
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    with pytest.raises(httpx2.HTTPStatusError) as exc_info:
        await discovery.discover(query, ask_indices=(0,))

    assert exc_info.value.response.status_code == 429


async def test_a_connection_error_propagates() -> None:
    """No HTTP response at all — the network-level failure case, distinct
    from a non-2xx status. Classification is discover_providers' job.
    """

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    discovery = _discovery(client)
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    with pytest.raises(httpx2.ConnectError):
        await discovery.discover(query, ask_indices=(0,))
