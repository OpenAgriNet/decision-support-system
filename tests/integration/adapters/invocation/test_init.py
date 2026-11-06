from __future__ import annotations

import httpx
import pytest

from dss.adapters.invocation.client import (
    HttpCapabilityInvocation,
    InitFailedError,
    build_init_request,
)
from dss.core.provider_discovery.models import FailureClass
from tests.support.providers import build_capability

SENDER_ID = "seeker-network-vistaar.da.gov.in"

PROVIDER = build_capability(
    provider_id="pmkisan",
    provider_name="PM-KISAN Portal",
    capability="openagrinet:Grievance",
    resource_id="res:pmkisan:grievance",
    bpp_id="provider-network-vistaar.da.gov.in",
    bpp_uri="https://provider-network-vistaar.da.gov.in/oan",
    observed_categories=("Grievance",),
)

# The pack's `pmkisan-on-demand-request-otp.json`.
ATTRIBUTES = {
    "@type": "openagrinet:Grievance",
    "informationMode": "OnDemand",
    "scheme": {"code": "PM-KISAN", "name": "Pradhan Mantri Kisan Samman Nidhi"},
    "enrolmentId": "UP12345678A",
}

# The pack's `pmkisan-on-demand-challenge-issued.json`.
_ISSUED = {
    "@type": "openagrinet:Grievance",
    "informationMode": "OnDemand",
    "scheme": {"code": "PM-KISAN", "name": "Pradhan Mantri Kisan Samman Nidhi"},
    "challenge": {
        "issued": {
            "method": "OTP",
            "channel": "SMS",
            "length": 4,
            "valueType": "NUMERIC",
            "expiresAt": "2026-09-28T11:08:00Z",
        }
    },
}

_REPLY = {
    "message": {
        "contract": {
            "commitments": [
                {
                    "resources": [
                        {"id": "res:pmkisan:grievance", "quantity": {"count": 1}}
                    ],
                    "commitmentAttributes": _ISSUED,
                }
            ]
        }
    }
}


def test_the_attributes_ride_on_the_first_commitment() -> None:
    request = build_init_request(
        PROVIDER,
        ATTRIBUTES,
        resource_id="res:pmkisan:grievance",
        contract_id="c0ffee00-0000-4000-8000-000000000001",
        sender_id=SENDER_ID,
        message_id="7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        timestamp="2026-10-29T06:12:01.330Z",
    )

    assert request["context"]["action"] == "init"
    assert request["context"]["receiverId"] == "provider-network-vistaar.da.gov.in"
    assert request["message"] == {
        "contract": {
            "id": "c0ffee00-0000-4000-8000-000000000001",
            "commitments": [
                {
                    "resources": [
                        {"id": "res:pmkisan:grievance", "quantity": {"count": 1}}
                    ],
                    "commitmentAttributes": ATTRIBUTES,
                }
            ],
        }
    }


class _Replies:
    def __init__(self, status_code: int, body: object = _REPLY) -> None:
        self._status_code = status_code
        self._body = body
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        return httpx.Response(self._status_code, json=self._body)


def _invocation(handler: _Replies) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id=SENDER_ID,
        backoff_seconds=0.0,
    )


async def test_the_issued_challenge_comes_back_from_init() -> None:
    handler = _Replies(200)

    reply = await _invocation(handler).init(PROVIDER, ATTRIBUTES, "txn-1")

    assert reply.attributes == _ISSUED
    assert handler.paths == ["/oan/init"]


async def test_a_transient_failure_is_sent_once_only() -> None:
    # Each try can text the farmer a new OTP.
    handler = _Replies(503)

    with pytest.raises(InitFailedError) as failed:
        await _invocation(handler).init(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.paths == ["/oan/init"]
    assert failed.value.failure_class is FailureClass.TRANSIENT
    assert failed.value.status_code == 503


async def test_an_unreadable_reply_is_a_defect() -> None:
    handler = _Replies(200, body={"message": {}})

    with pytest.raises(InitFailedError) as failed:
        await _invocation(handler).init(PROVIDER, ATTRIBUTES, "txn-1")

    assert failed.value.failure_class is FailureClass.DEFECT
