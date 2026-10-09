"""Contract tests for real values on a /select call (ADR-0015).

The request body arrives holding tags («phone_1»). Only the bytes on the wire
carry the real value; the logs show the tag, and a value the provider echoes
back is a tag again before anything reads the answer.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from dss.adapters.invocation.client import HttpCapabilityInvocation, SelectFailed
from dss.core.provider_discovery.models import ProviderCapability
from dss.core.redaction.reveal import RevealMap

CAPABILITY = ProviderCapability(
    provider_id="scheme-desk",
    provider_name="Scheme Desk",
    capability="openagrinet:AgricultureResource",
    resource_id="res:scheme-desk:application-status",
    observed_categories=("Scheme",),
)
PHONE = "9876543210"
REVEAL = RevealMap(values={"«phone_1»": PHONE})


def _answer(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {
                "contract": {
                    "commitments": [
                        {
                            "resources": [
                                {
                                    "id": "res:scheme-desk:application-status:1",
                                    "resourceAttributes": {
                                        "@type": CAPABILITY.capability,
                                        "status": text,
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        },
    )


class _Provider:
    def __init__(self, reply: httpx.Response) -> None:
        self._reply = reply
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return self._reply


def _invocation(provider: _Provider) -> HttpCapabilityInvocation:
    return HttpCapabilityInvocation(
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
        base_url="https://provider-network-vistaar.da.gov.in/oan",
        sender_id="seeker-network-vistaar.da.gov.in",
        receiver_id="provider-network-vistaar.da.gov.in",
        backoff_seconds=0.0,
    )


def _attributes(body: dict) -> dict:
    return body["message"]["contract"]["commitments"][0]["resources"][0][
        "resourceAttributes"
    ]


async def test_the_wire_carries_the_real_value() -> None:
    provider = _Provider(_answer("approved"))

    await _invocation(provider).select(
        CAPABILITY, {"applicant": {"phone": "«phone_1»"}}, "txn-1", reveal=REVEAL
    )

    assert _attributes(provider.bodies[0])["applicant"] == {"phone": PHONE}


async def test_a_tag_the_map_does_not_hold_goes_out_as_the_tag() -> None:
    # A destroyed Aadhaar has no entry, so no provider can be sent it.
    provider = _Provider(_answer("approved"))

    await _invocation(provider).select(
        CAPABILITY, {"id": "«aadhaar_1»"}, "txn-1", reveal=REVEAL
    )

    assert _attributes(provider.bodies[0])["id"] == "«aadhaar_1»"


async def test_without_a_map_nothing_is_revealed() -> None:
    provider = _Provider(_answer("approved"))

    await _invocation(provider).select(
        CAPABILITY, {"applicant": {"phone": "«phone_1»"}}, "txn-1"
    )

    assert _attributes(provider.bodies[0])["applicant"] == {"phone": "«phone_1»"}


async def test_an_echoed_value_is_a_tag_again_in_the_answer() -> None:
    provider = _Provider(_answer(f"application for {PHONE} is approved"))

    [answer] = await _invocation(provider).select(
        CAPABILITY, {"applicant": {"phone": "«phone_1»"}}, "txn-1", reveal=REVEAL
    )

    [resource] = answer.attributes["resources"]
    assert resource["status"] == "application for «phone_1» is approved"


async def test_the_logs_show_tags_never_the_value(caplog) -> None:
    provider = _Provider(_answer(f"application for {PHONE} is approved"))

    with caplog.at_level(logging.DEBUG, logger="dss.trace"):
        await _invocation(provider).select(
            CAPABILITY, {"applicant": {"phone": "«phone_1»"}}, "txn-1", reveal=REVEAL
        )

    assert "«phone_1»" in caplog.text
    assert PHONE not in caplog.text


async def test_a_failed_call_does_not_carry_the_value_in_its_detail() -> None:
    provider = _Provider(httpx.Response(400, text=f"bad phone {PHONE}"))

    with pytest.raises(SelectFailed) as failed:
        await _invocation(provider).select(
            CAPABILITY, {"applicant": {"phone": "«phone_1»"}}, "txn-1", reveal=REVEAL
        )

    # The detail reaches the planner's prompt; the value must not.
    assert PHONE not in (failed.value.detail or "")
