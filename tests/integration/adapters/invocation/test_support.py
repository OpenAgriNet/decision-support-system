"""Contract tests for the `/support` call: the envelope a form's attributes
ride in, the reply they come back in, and how a failed filing is reported."""

from __future__ import annotations

import json

import httpx
import pytest

from dss.adapters.invocation.client import (
    HttpCapabilityInvocation,
    SupportFailedError,
    build_support_request,
    map_support_response,
)
from dss.core.provider_discovery.models import FailureClass, ProviderCapability

SENDER_ID = "seeker-network-vistaar.da.gov.in"
RECEIVER_ID = "provider-network-vistaar.da.gov.in"

PROVIDER = ProviderCapability(
    provider_id="pmfby",
    provider_name="PMFBY Grievance Portal",
    capability="openagrinet:Grievance",
    resource_id="res:pmfby:grievance",
    observed_categories=("Grievance",),
)

ATTRIBUTES = {
    "@type": "openagrinet:Grievance",
    "provider": {"id": "pmfby", "descriptor": {"name": "PMFBY Grievance Portal"}},
    "scheme": {"code": "PMFBY"},
    "applicantMobile": "9876543210",
}


def test_the_attributes_ride_as_the_first_support_channel() -> None:
    """On `support` there is no contract to hang the payload from; the pack
    names `channels` as its container."""

    request = build_support_request(
        ATTRIBUTES,
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        timestamp="2026-10-29T06:12:01.330Z",
    )

    assert request["context"]["action"] == "support"
    assert request["context"]["senderId"] == SENDER_ID
    assert request["context"]["receiverId"] == RECEIVER_ID
    assert request["context"]["transactionId"] == "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44"
    assert request["message"] == {"support": {"channels": [ATTRIBUTES]}}


_FILED = {
    "message": {
        "support": {
            "channels": [
                {
                    "@type": "openagrinet:Grievance",
                    "scheme": {"code": "PMFBY"},
                    "case": {
                        "ticketNo": "100626000099001",
                        "status": {"code": "Registered"},
                        "filedOn": "2026-10-29",
                    },
                }
            ]
        }
    }
}


def test_the_reply_is_the_first_support_channel() -> None:
    assert map_support_response(_FILED) == _FILED["message"]["support"]["channels"][0]


def test_a_channel_that_is_not_an_object_is_malformed() -> None:
    with pytest.raises(TypeError):
        map_support_response({"message": {"support": {"channels": ["ticket"]}}})


class _Replies:
    """Answers ``status_code`` with ``headers`` for the first ``fail_times``
    calls, then files the grievance. Counts the calls."""

    def __init__(
        self,
        status_code: int,
        *,
        fail_times: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._status_code = status_code
        self._fail_times = fail_times
        self._headers = headers or {}
        self.calls = 0
        self.message_ids: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.message_ids.append(json.loads(request.content)["context"]["messageId"])
        if self.calls <= self._fail_times:
            return httpx.Response(self._status_code, headers=self._headers, json={})
        return httpx.Response(200, json=_FILED)


def _invocation(handler: _Replies, **kwargs) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        backoff_seconds=0.0,
        **kwargs,
    )


async def test_a_filed_grievance_comes_back_with_its_ticket() -> None:
    handler = _Replies(200, fail_times=0)

    reply = await _invocation(handler).support(PROVIDER, ATTRIBUTES, "txn-1")

    assert reply.attributes["case"]["ticketNo"] == "100626000099001"
    assert handler.calls == 1


async def test_a_transient_failure_is_retried_as_a_select_is() -> None:
    handler = _Replies(429, fail_times=2)

    reply = await _invocation(handler).support(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.calls == 3
    assert reply.attributes["case"]["ticketNo"] == "100626000099001"


async def test_a_rejected_filing_is_a_defect_with_no_retry_hint() -> None:
    handler = _Replies(400, fail_times=99)

    with pytest.raises(SupportFailedError) as failed:
        await _invocation(handler).support(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.calls == 1
    assert failed.value.failure_class is FailureClass.DEFECT
    assert failed.value.status_code == 400
    assert failed.value.retry_after_seconds is None


async def test_a_provider_naming_its_own_time_is_not_retried() -> None:
    """Retrying in half a second is the hammering a `Retry-After` asks to
    stop; the time reaches the farmer's reply instead."""

    handler = _Replies(503, fail_times=99, headers={"Retry-After": "120"})

    with pytest.raises(SupportFailedError) as failed:
        await _invocation(handler).support(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.calls == 1
    assert failed.value.failure_class is FailureClass.TRANSIENT
    assert failed.value.retry_after_seconds == 120


async def test_a_retried_filing_keeps_its_message_id() -> None:
    """The nearest thing to a duplicate key until the pack gives one: a
    provider that filed but answered late sees the same message again."""

    handler = _Replies(429, fail_times=2)

    await _invocation(handler).support(PROVIDER, ATTRIBUTES, "txn-1")

    assert len(handler.message_ids) == 3
    assert len(set(handler.message_ids)) == 1
