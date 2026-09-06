"""Builds /select requests and maps select responses.

Pure translation only — no business logic. resourceAttributes is already
fully assembled by the caller; this module only wraps it in the Beckn
envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx2

from dss.adapters.network_common import (
    NO_STATUS_CODE,
    classify_status_code,
    extract_validity,
)
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)

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


class SelectFailed(Exception):
    """A /select call failed — the caller turns this into a core Failure."""

    def __init__(
        self,
        capability: str,
        status_code: int,
        failure_class: FailureClass,
        detail: str | None,
    ) -> None:
        super().__init__(f"select failed for {capability}: {status_code} ({detail})")
        self.capability = capability
        self.status_code = status_code
        self.failure_class = failure_class
        self.detail = detail


class HttpCapabilityInvocation:
    """Implements CapabilityInvocation over the Network Adapter's /select.

    Owns the real HTTP call; request-building and response-mapping stay as
    the pure functions above so they're testable without a client at all.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        base_url: str,
        sender_id: str,
        receiver_id: str,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._sender_id = sender_id
        self._receiver_id = receiver_id

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer:
        request_body = build_select_request(
            capability,
            resource_attributes,
            resource_id=str(uuid4()),
            sender_id=self._sender_id,
            receiver_id=self._receiver_id,
            message_id=str(uuid4()),
            transaction_id=transaction_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/select", json=request_body
            )
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise SelectFailed(
                capability.capability,
                status_code,
                classify_status_code(status_code),
                exc.response.text,
            ) from exc
        except httpx2.HTTPError as exc:
            raise SelectFailed(
                capability.capability,
                NO_STATUS_CODE,
                classify_status_code(NO_STATUS_CODE),
                str(exc),
            ) from exc

        return map_select_response(
            response.json(),
            provider_id=capability.provider_id,
            provider_name=capability.provider_name,
        )
