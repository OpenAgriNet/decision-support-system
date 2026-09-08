"""Contract tests for HttpCapabilityDiscovery — the real network hop.

Uses httpx2.MockTransport so no real socket is ever opened, per tier 2's
'recorded fixture or local test server, never live network calls' rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx2

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.core.provider_discovery.models import FailureClass, ProviderQuery

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
    )


async def test_discover_posts_to_the_discover_endpoint_and_maps_the_response() -> None:
    on_discover = json.loads((FIXTURES / "discover_response.json").read_text())
    discovery = _discovery(_client_returning(on_discover))
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    result = await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-from-experience-layer"
    )

    assert result.capabilities[0][0].provider_id == "mausamgram"


async def test_discover_sends_the_given_transaction_id() -> None:
    """The transactionId correlates a whole request across discover/select —
    it must be whatever the Experience layer supplied, not one we invent."""
    sent_bodies = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent_bodies.append(json.loads(request.content))
        on_discover = json.loads((FIXTURES / "discover_response.json").read_text())
        return httpx2.Response(200, json=on_discover)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    discovery = _discovery(client)
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-from-experience-layer"
    )

    assert sent_bodies[0]["context"]["transactionId"] == "txn-from-experience-layer"


async def test_a_refreshed_cache_is_reflected_without_rewiring() -> None:
    """Proves the adapter reads the cache live, not a construction-time
    snapshot — the schema_context_index dict is mutated after wiring.
    """
    on_discover = json.loads((FIXTURES / "discover_response.json").read_text())
    index: dict[str, tuple[str, str]] = {}
    cache = _FakeSchemaPackCache(index)
    discovery = _discovery(_client_returning(on_discover), schema_pack_cache=cache)
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    index["openagrinet:WeatherObservation"] = ("WeatherObservation", "v0.1")
    result = await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-test"
    )

    assert result.capabilities[0][0].provider_id == "mausamgram"


async def test_a_non_2xx_response_is_returned_as_a_failure() -> None:
    """discover() never raises — failures are data. Status-code
    classification itself is covered in test_failure_classification.py.
    """
    discovery = _discovery(
        _client_returning({"error": "rate limited"}, status_code=429)
    )
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    result = await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-test"
    )

    assert result.failures[0][0].status_code == 429
    assert result.capabilities[0] == ()


async def test_a_response_that_cannot_be_mapped_is_returned_as_a_defect() -> None:
    """A 200 carrying a shape we can't read must not raise: discover()
    failing by exception would cancel every sibling query in the caller's
    task group. It's a defect on one side or the other, never retry-worthy.
    """
    discovery = _discovery(_client_returning({"message": {"catalogs": [{}]}}))
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    result = await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-test"
    )

    failure = result.failures[0][0]
    assert failure.failure_class == FailureClass.DEFECT
    assert failure.capability == "openagrinet:WeatherObservation"
    assert result.answers[0] == ()
    assert result.capabilities[0] == ()


async def test_a_body_that_is_not_json_is_returned_as_a_defect() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>gateway splash page</html>")

    discovery = _discovery(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    query = ProviderQuery(
        capabilities=("openagrinet:WeatherObservation",),
        languages=("hi",),
        coverage=None,
    )

    result = await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-test"
    )

    assert result.failures[0][0].failure_class == FailureClass.DEFECT


async def test_a_connection_error_is_returned_as_a_failure() -> None:
    """No HTTP response at all — the network-level failure case, distinct
    from a non-2xx status, but still returned as data, not raised.
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

    result = await discovery.discover(
        query, ask_indices=(0,), transaction_id="txn-test"
    )

    assert result.failures[0][0].status_code == 0
