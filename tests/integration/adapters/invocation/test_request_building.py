"""Contract tests for building /select requests.

No business logic — only that a given ProviderCapability + resourceAttributes
translates into the right wire shape.
"""

from __future__ import annotations

from dss.adapters.invocation.client import build_select_request
from dss.core.provider_discovery.models import ProviderCapability

SENDER_ID = "seeker-network-vistaar.da.gov.in"
RECEIVER_ID = "provider-network-vistaar.da.gov.in"


def _capability() -> ProviderCapability:
    return ProviderCapability(
        provider_id="mausamgram",
        provider_name="IMD Mausamgram NWP",
        capability="openagrinet:WeatherObservation",
        resource_id="res:mausamgram:point-forecast",
        observed_categories=("Weather",),
    )


def _resource_attributes() -> dict:
    return {
        "@context": "https://schemas.openagrinet.global/schema/WeatherObservation/v0.1/context.jsonld",
        "@type": "openagrinet:WeatherObservation",
        "subjectCategories": ["Weather"],
        "location": {"geo": {"type": "Point", "coordinates": [73.7898, 19.9975]}},
    }


def test_builds_the_envelope_from_given_ids_and_timestamp() -> None:
    request = build_select_request(
        _capability(),
        _resource_attributes(),
        resource_id="3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        timestamp="2026-08-26T06:12:01.330Z",
    )

    assert request["context"]["action"] == "select"
    assert request["context"]["version"] == "2.0.0"
    assert request["context"]["senderId"] == SENDER_ID
    assert request["context"]["receiverId"] == RECEIVER_ID
    assert request["context"]["messageId"] == "7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22"
    assert request["context"]["transactionId"] == "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44"
    assert request["context"]["timestamp"] == "2026-08-26T06:12:01.330Z"


def test_status_descriptor_defaults_to_draft() -> None:
    request = build_select_request(
        _capability(),
        _resource_attributes(),
        resource_id="3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:12:01.330Z",
    )

    commitment = request["message"]["contract"]["commitments"][0]
    assert commitment["status"]["descriptor"] == {"code": "DRAFT", "name": "Draft"}


def test_resource_carries_the_fresh_id_and_attributes() -> None:
    request = build_select_request(
        _capability(),
        _resource_attributes(),
        resource_id="3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:12:01.330Z",
    )

    commitment = request["message"]["contract"]["commitments"][0]
    resource = commitment["resources"][0]
    assert resource["id"] == "3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90"
    assert resource["resourceAttributes"] == _resource_attributes()


def test_offer_references_the_fresh_resource_id_not_discoverys() -> None:
    request = build_select_request(
        _capability(),
        _resource_attributes(),
        resource_id="3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:12:01.330Z",
    )

    commitment = request["message"]["contract"]["commitments"][0]
    offer = commitment["offer"]
    assert offer["resourceIds"] == ["3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90"]
    assert offer["provider"]["id"] == "mausamgram"
    assert offer["provider"]["descriptor"] == {"name": "IMD Mausamgram NWP"}
    assert "code" not in offer["provider"]["descriptor"]


def test_offer_id_defaults_to_a_configured_constant() -> None:
    request = build_select_request(
        _capability(),
        _resource_attributes(),
        resource_id="3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:12:01.330Z",
    )

    offer = request["message"]["contract"]["commitments"][0]["offer"]
    assert offer["id"] == "offer:open-data"


def test_offer_id_is_configurable() -> None:
    request = build_select_request(
        _capability(),
        _resource_attributes(),
        resource_id="3f6a9d2e-8c41-4b0a-9e77-1d2f5c8a6b90",
        sender_id=SENDER_ID,
        receiver_id=RECEIVER_ID,
        message_id="m",
        transaction_id="t",
        timestamp="2026-08-26T06:12:01.330Z",
        offer_id="offer:acme:custom",
    )

    offer = request["message"]["contract"]["commitments"][0]["offer"]
    assert offer["id"] == "offer:acme:custom"
