"""Contract tests for classifying a failed /select call.

429/500/NET_* are transient; 400/401/403 are defects — same classification
discover already uses.
"""

from __future__ import annotations

import httpx2
import pytest

from dss.adapters.invocation.client import HttpCapabilityInvocation, SelectFailed
from dss.core.provider_discovery.models import FailureClass, ProviderCapability

CAPABILITY = ProviderCapability(
    provider_id="mausamgram",
    provider_name="IMD Mausamgram NWP",
    capability="openagrinet:WeatherObservation",
    resource_id="res:mausamgram:point-forecast",
    observed_categories=("Weather",),
)


def _invocation_returning_status(status_code: int) -> HttpCapabilityInvocation:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status_code, json={"error": "simulated"})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return HttpCapabilityInvocation(
        client=client,
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        attempts=1,  # classification, not retry — see test_retry.py
    )


def _invocation_raising_connection_error() -> HttpCapabilityInvocation:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return HttpCapabilityInvocation(
        client=client,
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        attempts=1,  # classification, not retry — see test_retry.py
    )


@pytest.mark.parametrize("status_code", [429, 500])
async def test_transient_status_codes_raise_selectfailed_transient(
    status_code: int,
) -> None:
    invocation = _invocation_returning_status(status_code)

    with pytest.raises(SelectFailed) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.status_code == status_code
    assert exc_info.value.failure_class == FailureClass.TRANSIENT
    assert exc_info.value.capability == "openagrinet:WeatherObservation"
    assert "simulated" in exc_info.value.detail


@pytest.mark.parametrize("status_code", [400, 401, 403])
async def test_defect_status_codes_raise_selectfailed_defect(status_code: int) -> None:
    invocation = _invocation_returning_status(status_code)

    with pytest.raises(SelectFailed) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.status_code == status_code
    assert exc_info.value.failure_class == FailureClass.DEFECT


async def test_a_connection_error_raises_selectfailed_transient() -> None:
    invocation = _invocation_raising_connection_error()

    with pytest.raises(SelectFailed) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.failure_class == FailureClass.TRANSIENT
    assert exc_info.value.status_code == 0
    assert "connection refused" in exc_info.value.detail


def _invocation_returning_body(body: dict) -> HttpCapabilityInvocation:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=body)

    return HttpCapabilityInvocation(
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        attempts=1,
    )


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("no contract", {"message": {}}),
        ("no commitments", {"message": {"contract": {}}}),
        ("empty commitments", {"message": {"contract": {"commitments": []}}}),
        (
            "resource missing resourceAttributes",
            {"message": {"contract": {"commitments": [{"resources": [{"id": "r"}]}]}}},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
async def test_a_malformed_200_raises_selectfailed_not_a_bare_keyerror(
    label: str, body: dict
) -> None:
    """The port's contract is "an answer or ``SelectFailed``". The response
    mapper sat outside the try, so a 200 with an unreadable body raised
    ``KeyError``/``IndexError`` straight past the planner tool's handler and
    aborted the whole agent run — losing every other ask's answers.

    The discovery adapter already does this (``_malformed_result``); select
    was missing the equivalent.
    """

    invocation = _invocation_returning_body(body)

    with pytest.raises(SelectFailed) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    # a defect, not transient: the same body will fail the same way
    assert exc_info.value.failure_class == FailureClass.DEFECT
    assert exc_info.value.capability == "openagrinet:WeatherObservation"


async def test_a_malformed_200_is_not_retried() -> None:
    """A defect means retrying sends the same request and reads the same bad
    body. Three attempts would only delay the failure."""

    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, json={"message": {}})

    invocation = HttpCapabilityInvocation(
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        backoff_seconds=0.0,
    )

    with pytest.raises(SelectFailed):
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert calls == 1
