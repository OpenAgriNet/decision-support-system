"""What a provider says when it refuses a form call, kept for the farmer.

Read only from a refusal (4xx), never from an outage or a refusal of our own
credentials; held values come back as tags."""

from __future__ import annotations

import httpx
import pytest

from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.ports.invocation import ProviderActionFailedError
from tests.support.providers import build_capability

PROVIDER = build_capability(
    provider_id="pmkisan",
    provider_name="PM-KISAN",
    capability="openagrinet:Grievance",
    resource_id="res:pmkisan:grievance",
    bpp_id="provider-network-vistaar.da.gov.in",
    bpp_uri="https://provider-network-vistaar.da.gov.in/oan",
    observed_categories=("Grievance",),
)

WRONG_OTP = {
    "message": {
        "status": "NACK",
        "messageId": "c511e377-bb5f-4e84-a4a1-048ec8f1cf4e",
        "error": {
            "code": "BIZ_GENERIC_ERROR",
            "message": "the OTP is wrong or has expired",
        },
    }
}


def _invocation(response: httpx.Response) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        network_id="host.oan",
        backoff_seconds=0.0,
    )


async def _refusal(
    response: httpx.Response, *, visibility: Visibility = NOTHING_HELD
) -> ProviderActionFailedError:
    with pytest.raises(ProviderActionFailedError) as failed:
        await _invocation(response).support(
            PROVIDER, {}, "txn-1", visibility=visibility
        )
    return failed.value


async def test_a_refusal_keeps_the_providers_message() -> None:
    failed = await _refusal(httpx.Response(400, json=WRONG_OTP))

    assert failed.provider_message == "the OTP is wrong or has expired"


async def test_a_refusal_with_its_error_at_the_top_keeps_the_message() -> None:
    body = {
        "message": {"ack": {"status": "NACK"}},
        "error": {"message": "No such case"},
    }

    failed = await _refusal(httpx.Response(404, json=body))

    assert failed.provider_message == "No such case"


async def test_the_message_is_kept_on_one_line() -> None:
    body = {"error": {"message": "  the OTP\n is\twrong  "}}

    failed = await _refusal(httpx.Response(400, json=body))

    assert failed.provider_message == "the OTP is wrong"


async def test_a_held_value_in_the_message_comes_back_as_its_tag() -> None:
    body = {"error": {"message": "no record for 9876543210"}}
    held = Visibility(values={"«phone_1»": "9876543210"})

    failed = await _refusal(httpx.Response(400, json=body), visibility=held)

    assert failed.provider_message == "no record for «phone_1»"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={}),
        httpx.Response(400, json={"error": "the OTP is wrong"}),
        httpx.Response(400, json={"error": {"message": "   "}}),
        httpx.Response(400, text="the OTP is wrong"),
        httpx.Response(400, json={"error": {"message": "x" * 201}}),
    ],
    ids=["no-error", "error-is-text", "blank", "not-json", "too-long"],
)
async def test_a_refusal_with_no_usable_message_keeps_none(
    response: httpx.Response,
) -> None:
    failed = await _refusal(response)

    assert failed.provider_message is None


@pytest.mark.parametrize("status_code", [401, 403, 408, 429, 500, 503])
async def test_a_failure_that_is_not_a_refusal_keeps_no_message(
    status_code: int,
) -> None:
    failed = await _refusal(httpx.Response(status_code, json=WRONG_OTP))

    assert failed.provider_message is None


async def test_a_joiner_inside_a_word_is_kept() -> None:
    word = "क्\u200dष"
    failed = await _refusal(httpx.Response(400, json={"error": {"message": word}}))

    assert failed.provider_message == word


async def test_a_message_over_the_limit_once_masked_keeps_none() -> None:
    phone = "9876543210"
    message = "x" * (199 - len(phone)) + " " + phone
    held = Visibility(values={"«applicant_phone_1»": phone})

    failed = await _refusal(
        httpx.Response(400, json={"error": {"message": message}}), visibility=held
    )

    assert failed.provider_message is None


async def test_a_text_error_beside_a_usable_one_keeps_the_usable_one() -> None:
    body = {"message": {"error": {"message": "the OTP is wrong"}}, "error": "NACK"}

    failed = await _refusal(httpx.Response(400, json=body))

    assert failed.provider_message == "the OTP is wrong"
