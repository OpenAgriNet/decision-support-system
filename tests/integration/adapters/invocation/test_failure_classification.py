"""Contract tests for reading a failed /select call into its kind."""

from __future__ import annotations

import json

import httpx
import pytest

from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.core.shared.models import ProviderFailureKind
from dss.ports.invocation import ProviderCallFailedError
from tests.support.providers import build_capability

CAPABILITY = build_capability(
    provider_id="mausamgram",
    provider_name="IMD Mausamgram NWP",
    capability="openagrinet:WeatherObservation",
    resource_id="res:mausamgram:point-forecast",
    observed_categories=("Weather",),
)


def _invocation_returning_status(
    status_code: int, *, body: str = '{"error": "simulated"}'
) -> HttpCapabilityInvocation:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpCapabilityInvocation(
        client=client,
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        network_id="host.oan",
        attempts=1,  # classification, not retry — see test_retry.py
    )


def _invocation_raising_connection_error() -> HttpCapabilityInvocation:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpCapabilityInvocation(
        client=client,
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        network_id="host.oan",
        attempts=1,  # classification, not retry — see test_retry.py
    )


@pytest.mark.parametrize(
    ("status_code", "kind"),
    [
        (500, ProviderFailureKind.OUTCOME_UNKNOWN),
        (400, ProviderFailureKind.REFUSED),
    ],
)
async def test_a_failed_select_carries_its_endpoint_and_kind(
    status_code: int, kind: ProviderFailureKind
) -> None:
    invocation = _invocation_returning_status(status_code)

    with pytest.raises(ProviderCallFailedError) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.endpoint == "select"
    assert exc_info.value.kind is kind
    assert exc_info.value.capability == "openagrinet:WeatherObservation"


async def test_a_select_refusal_keeps_its_body() -> None:
    body = '{"message":{"status":"NACK","error":{"message":"No such district."}}}'
    invocation = _invocation_returning_status(404, body=body)

    with pytest.raises(ProviderCallFailedError) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.reason == body


async def test_a_connection_error_is_not_sent_with_no_reason() -> None:
    invocation = _invocation_raising_connection_error()

    with pytest.raises(ProviderCallFailedError) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.kind is ProviderFailureKind.NOT_SENT
    assert exc_info.value.reason is None


def _invocation_returning_body(body: dict | bytes) -> HttpCapabilityInvocation:
    content = body if isinstance(body, bytes) else json.dumps(body).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        network_id="host.oan",
        attempts=1,
    )


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("no contract", {"message": {}}),
        ("no commitments", {"message": {"contract": {}}}),
        (
            "resource missing resourceAttributes",
            {"message": {"contract": {"commitments": [{"resources": [{"id": "r"}]}]}}},
        ),
        ("not UTF-8", b"\x80<html>gateway splash page</html>"),
        (
            "source without a sourceId",
            {
                "message": {
                    "contract": {
                        "commitments": [
                            {
                                "resources": [
                                    {
                                        "id": "r",
                                        "resourceAttributes": {
                                            "@type": "openagrinet:WeatherObservation",
                                            "source": {"sourceName": "IMD"},
                                        },
                                    }
                                ]
                            }
                        ]
                    }
                }
            },
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
async def test_a_malformed_200_raises_the_call_failure_not_a_bare_keyerror(
    label: str, body: dict | bytes
) -> None:
    """The port's contract is "an answer or ``ProviderCallFailedError``". The response
    mapper sat outside the try, so a 200 with an unreadable body raised
    ``KeyError``/``IndexError`` straight past the planner tool's handler and
    aborted the whole agent run — losing every other ask's answers.

    The discovery adapter already does this (``_malformed_result``); select
    was missing the equivalent.
    """

    invocation = _invocation_returning_body(body)

    with pytest.raises(ProviderCallFailedError) as exc_info:
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert exc_info.value.kind is ProviderFailureKind.OUTCOME_UNKNOWN
    assert exc_info.value.capability == "openagrinet:WeatherObservation"


async def test_a_200_that_committed_to_nothing_is_empty_not_malformed() -> None:
    """An empty commitments list is readable — it says the provider found
    nothing. That is an answer of zero results, not a body we could not parse,
    so the caller gets an empty sequence and can try another candidate."""

    invocation = _invocation_returning_body(
        {"message": {"contract": {"commitments": []}}}
    )

    assert await invocation.select(CAPABILITY, {}, transaction_id="txn-test") == []


async def test_a_malformed_200_is_not_retried() -> None:
    """Retrying sends the same request and reads the same bad body. Three
    attempts would only delay the failure."""

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {}})

    invocation = HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        network_id="host.oan",
        backoff_seconds=0.0,
    )

    with pytest.raises(ProviderCallFailedError):
        await invocation.select(CAPABILITY, {}, transaction_id="txn-test")

    assert calls == 1
