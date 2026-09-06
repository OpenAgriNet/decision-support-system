"""Builds /select requests and maps select responses.

Pure translation only — no business logic. resourceAttributes is already
fully assembled by the caller; this module only wraps it in the Beckn
envelope.
"""

from __future__ import annotations

from typing import Any

from dss.adapters.network_common import extract_validity
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability

_SELECT_VERSION = "2.0.0"
_DEFAULT_STATUS_DESCRIPTOR = {"code": "DRAFT", "name": "Draft"}
_DEFAULT_OFFER_ID = "offer:open-data"


def build_select_request(
    capability: ProviderCapability,
    resource_attributes: dict,
    *,
    resource_id: str,
    sender_id: str,
    receiver_id: str,
    message_id: str,
    transaction_id: str,
    timestamp: str,
    status_descriptor: dict = _DEFAULT_STATUS_DESCRIPTOR,
    offer_id: str = _DEFAULT_OFFER_ID,
) -> dict[str, Any]:
    return {
        "context": {
            "action": "select",
            "version": _SELECT_VERSION,
            "senderId": sender_id,
            "receiverId": receiver_id,
            "messageId": message_id,
            "transactionId": transaction_id,
            "timestamp": timestamp,
        },
        "message": {
            "contract": {
                "commitments": [
                    {
                        "status": {"descriptor": status_descriptor},
                        "resources": [
                            {
                                "id": resource_id,
                                "resourceAttributes": resource_attributes,
                            }
                        ],
                        "offer": {
                            "id": offer_id,
                            "resourceIds": [resource_id],
                            "provider": {
                                "id": capability.provider_id,
                                "descriptor": {"name": capability.provider_name},
                            },
                        },
                    }
                ]
            }
        },
    }


def map_select_response(
    response: dict[str, Any], *, provider_id: str, provider_name: str
) -> DiscoveredAnswer:
    """The provider may assign its own resource id in the response — distinct
    from the fresh id we sent in the request."""

    commitment = response["message"]["contract"]["commitments"][0]
    resource = commitment["resources"][0]
    attributes = resource["resourceAttributes"]
    return DiscoveredAnswer(
        provider_id=provider_id,
        provider_name=provider_name,
        capability=attributes["@type"],
        resource_id=resource["id"],
        attributes=attributes,
        validity=extract_validity(attributes),
    )
