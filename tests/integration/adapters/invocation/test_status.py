"""Contract tests for the `/status` call: the envelope a case's attributes ride
in, the reply they come back in, and how a failed read is reported."""

from __future__ import annotations

import json

import httpx
import pytest

from dss.adapters.invocation.client import (
    HttpCapabilityInvocation,
    StatusFailedError,
    build_status_request,
    map_status_response,
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

# The pack's read-case example, `pmfby-on-demand-read-case.json`.
ATTRIBUTES = {
    "@type": "openagrinet:Grievance",
    "informationMode": "OnDemand",
    "scheme": {"code": "PMFBY", "name": "Pradhan Mantri Fasal Bima Yojana"},
    "applicantMobile": "9876543210",
    "case": {"ticketNo": "100626000099001"},
}


def test_the_attributes_ride_on_the_first_commitment() -> None:
    """On `status` the pack names `commitmentAttributes` as the container,
    beside the desk's one thin resource."""

    request = build_status_request(
        ATTRIBUTES,
        resource_id="res:pmfby:grievance",
        contract_id="c0ffee00-0000-4000-8000-000000000001",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        timestamp="2026-10-29T06:12:01.330Z",
    )

    assert request["context"]["action"] == "status"
    assert request["context"]["senderId"] == SENDER_ID
    assert request["context"]["receiverId"] == RECEIVER_ID
    assert request["context"]["transactionId"] == "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44"
    assert request["message"] == {
        "contract": {
            "id": "c0ffee00-0000-4000-8000-000000000001",
            "commitments": [
                {
                    "resources": [
                        {"id": "res:pmfby:grievance", "quantity": {"count": 1}}
                    ],
                    "commitmentAttributes": ATTRIBUTES,
                }
            ],
        }
    }


_CASE = {
    "@type": "openagrinet:Grievance",
    "scheme": {"code": "PMFBY"},
    "case": {
        "ticketNo": "100626000099001",
        "status": {"code": "UnderReview"},
        "filedOn": "2026-09-28",
        "cropName": "Paddy",
    },
    "grievance": {"description": "Cannot see my Kharif 2026 enrolment."},
}

_READ = {
    "message": {
        "contract": {
            "commitments": [
                {
                    "resources": [
                        {"id": "res:pmfby:grievance", "quantity": {"count": 1}}
                    ],
                    "commitmentAttributes": _CASE,
                }
            ]
        }
    }
}


def test_the_reply_is_the_first_commitments_attributes() -> None:
    assert map_status_response(_READ) == _CASE


def test_attributes_that_are_not_an_object_are_malformed() -> None:
    with pytest.raises(TypeError):
        map_status_response(
            {"message": {"contract": {"commitments": [{"commitmentAttributes": []}]}}}
        )


class _Replies:
    """Answers ``status_code`` with ``headers`` for the first ``fail_times``
    calls, then returns the case. Counts the calls."""

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
        self.paths: list[str] = []
        self.message_ids: list[str] = []
        self.contract_ids: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content)
        self.paths.append(request.url.path)
        self.message_ids.append(body["context"]["messageId"])
        self.contract_ids.append(body["message"]["contract"]["id"])
        if self.calls <= self._fail_times:
            return httpx.Response(self._status_code, headers=self._headers, json={})
        return httpx.Response(200, json=_READ)


def _invocation(handler: _Replies) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        backoff_seconds=0.0,
    )


async def test_a_read_case_comes_back_from_status() -> None:
    handler = _Replies(200, fail_times=0)

    reply = await _invocation(handler).status(PROVIDER, ATTRIBUTES, "txn-1")

    assert reply.attributes["case"]["status"] == {"code": "UnderReview"}
    assert handler.paths == ["/oan/status"]


async def test_a_transient_failure_is_retried_as_a_select_is() -> None:
    handler = _Replies(503, fail_times=2)

    reply = await _invocation(handler).status(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.calls == 3
    assert reply.attributes["case"]["cropName"] == "Paddy"


async def test_a_rejected_read_is_a_defect_and_not_retried() -> None:
    """How a desk says it has no such case: the form path tells the farmer
    no complaint was found for that ticket and phone."""

    handler = _Replies(400, fail_times=99)

    with pytest.raises(StatusFailedError) as failed:
        await _invocation(handler).status(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.calls == 1
    assert failed.value.failure_class is FailureClass.DEFECT
    assert failed.value.status_code == 400
    assert failed.value.retry_after_seconds is None


async def test_a_provider_naming_its_own_time_is_not_retried() -> None:
    handler = _Replies(503, fail_times=99, headers={"Retry-After": "120"})

    with pytest.raises(StatusFailedError) as failed:
        await _invocation(handler).status(PROVIDER, ATTRIBUTES, "txn-1")

    assert handler.calls == 1
    assert failed.value.failure_class is FailureClass.TRANSIENT
    assert failed.value.retry_after_seconds == 120


async def test_a_retried_read_keeps_its_message_and_contract() -> None:
    handler = _Replies(503, fail_times=2)

    await _invocation(handler).status(PROVIDER, ATTRIBUTES, "txn-1")

    assert len(set(handler.message_ids)) == 1
    assert len(set(handler.contract_ids)) == 1


async def test_each_read_is_a_new_contract() -> None:
    """The DSS keeps nothing between checks: a second read is a new session."""

    handler = _Replies(200, fail_times=0)
    invocation = _invocation(handler)

    await invocation.status(PROVIDER, ATTRIBUTES, "txn-1")
    await invocation.status(PROVIDER, ATTRIBUTES, "txn-2")

    assert len(set(handler.contract_ids)) == 2
