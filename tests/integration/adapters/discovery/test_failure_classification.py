"""Contract tests for classifying a failed /discover call.

429/500/NET_* are transient; 400/401/403 are defects.
"""

from __future__ import annotations

import httpx
import pytest

from dss.adapters.discovery.client import HttpCapabilityDiscovery
from dss.core.provider_discovery.models import FailureClass, ProviderQuery


class _FakeSchemaPackCache:
    def current_schema_context(self):
        return {
            "openagrinet:MandiPrice": ("MandiPrice", "v0.1"),
            "openagrinet:MarketIntelligence": ("MarketIntelligence", "v0.1"),
        }


def _discovery_returning_status(status_code: int) -> HttpCapabilityDiscovery:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "simulated"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpCapabilityDiscovery(
        client=client,
        base_url="https://discovery-network-vistaar.da.gov.in/oan",
        schema_pack_cache=_FakeSchemaPackCache(),
        network_host_id="host.oan",
    )


def _discovery_raising_connection_error() -> HttpCapabilityDiscovery:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpCapabilityDiscovery(
        client=client,
        base_url="https://discovery-network-vistaar.da.gov.in/oan",
        schema_pack_cache=_FakeSchemaPackCache(),
        network_host_id="host.oan",
    )


QUERY = ProviderQuery(
    capabilities=("openagrinet:MandiPrice",),
    subject_category="Market",
    languages=("hi",),
    coverage=None,
)


@pytest.mark.parametrize("status_code", [429, 500])
async def test_transient_status_codes_classify_as_transient(status_code: int) -> None:
    discovery = _discovery_returning_status(status_code)

    result = await discovery.discover(
        QUERY, transaction_id="txn-test", sender_id="consumer.oan.dev"
    )

    failure = result.failures[0]
    assert failure.status_code == status_code
    assert failure.failure_class == FailureClass.TRANSIENT
    assert failure.capability == "openagrinet:MandiPrice"
    assert failure.detail is not None
    assert "simulated" in failure.detail


@pytest.mark.parametrize("status_code", [400, 401, 403])
async def test_defect_status_codes_classify_as_defect(status_code: int) -> None:
    discovery = _discovery_returning_status(status_code)

    result = await discovery.discover(
        QUERY, transaction_id="txn-test", sender_id="consumer.oan.dev"
    )

    failure = result.failures[0]
    assert failure.status_code == status_code
    assert failure.failure_class == FailureClass.DEFECT


async def test_a_connection_error_classifies_as_transient() -> None:
    discovery = _discovery_raising_connection_error()

    result = await discovery.discover(
        QUERY, transaction_id="txn-test", sender_id="consumer.oan.dev"
    )

    failure = result.failures[0]
    assert failure.failure_class == FailureClass.TRANSIENT
    assert failure.status_code == 0
    assert failure.detail is not None
    assert "connection refused" in failure.detail


async def test_a_failed_call_produces_empty_answers_and_capabilities() -> None:
    discovery = _discovery_returning_status(429)

    result = await discovery.discover(
        QUERY, transaction_id="txn-test", sender_id="consumer.oan.dev"
    )

    assert result.answers == ()
    assert result.capabilities == ()


async def test_a_query_with_two_capabilities_gets_a_failure_entry_each() -> None:
    two_capability_query = ProviderQuery(
        capabilities=("openagrinet:MandiPrice", "openagrinet:MarketIntelligence"),
        subject_category="Market",
        languages=("hi",),
        coverage=None,
    )
    discovery = _discovery_returning_status(500)

    result = await discovery.discover(
        two_capability_query, transaction_id="txn-test", sender_id="consumer.oan.dev"
    )

    capabilities_failed = {f.capability for f in result.failures}
    assert capabilities_failed == {
        "openagrinet:MandiPrice",
        "openagrinet:MarketIntelligence",
    }


async def test_a_failed_category_only_query_is_still_reported() -> None:
    """Failures are listed per capability, and a scheme query has none — so the
    subject category stands in, or a 500 would read as "nobody serves it" (#52).
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream is down")

    query = ProviderQuery(
        capabilities=(),
        subject_category="Scheme",
        languages=("en",),
        coverage=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        discovery = HttpCapabilityDiscovery(
            client=client,
            base_url="http://network.test",
            schema_pack_cache=_FakeSchemaPackCache(),
            network_host_id="host.oan",
        )
        result = await discovery.discover(
            query, transaction_id="t", sender_id="consumer.oan.dev"
        )

    assert len(result.failures) == 1
    assert result.failures[0].capability == "Scheme"
    assert result.failures[0].status_code == 500
