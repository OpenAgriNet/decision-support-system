"""Builds /select requests and maps select responses.

Pure translation only — no business logic. resourceAttributes is already
fully assembled by the caller; this module only wraps it in the Beckn
envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import anyio
import httpx

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
from dss.observability.trace_log import log_external_response

# Defined on the port, not here: a failed select is part of the contract, so
# a caller can catch it without importing this adapter. Re-exported because
# this module raises it.
from dss.ports.invocation import SelectFailed

__all__ = ["HttpCapabilityInvocation", "SelectFailed", "build_select_request"]

# A 200 whose body we cannot read. KeyError/IndexError for a missing key or
# an empty commitments list, TypeError for a wrong shape, ValueError because
# json.JSONDecodeError is one — a body that is not JSON at all.
_MALFORMED_RESPONSE = (KeyError, IndexError, TypeError, ValueError)

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


class HttpCapabilityInvocation:
    """Implements CapabilityInvocation over the Network Adapter's /select.

    Owns the real HTTP call; request-building and response-mapping stay as
    the pure functions above so they're testable without a client at all.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        sender_id: str,
        receiver_id: str,
        attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._sender_id = sender_id
        self._receiver_id = receiver_id
        self._attempts = attempts
        self._backoff_seconds = backoff_seconds

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer:
        """Call /select, retrying a transient failure.

        Only ``TRANSIENT`` failures are retried: a ``DEFECT`` (400/401/403) is
        a malformed request or bad credentials, so sending it again changes
        nothing and delays the failure the caller needs.

        Backoff doubles per attempt (0.5s, 1s, ...) rather than retrying at
        once, because ``429`` is also transient and hammering a rate-limited
        provider is what caused it.
        """

        for attempt in range(1, self._attempts + 1):
            try:
                return await self._select_once(
                    capability, resource_attributes, transaction_id
                )
            except SelectFailed as failure:
                last_chance = attempt == self._attempts
                if failure.failure_class is not FailureClass.TRANSIENT or last_chance:
                    raise
                await anyio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop either returns or raises")

    async def _select_once(
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
            log_external_response(
                "invocation",
                transaction_id,
                status=response.status_code,
                capability=capability.capability,
                body=response.text,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise SelectFailed(
                capability.capability,
                status_code,
                classify_status_code(status_code),
                exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            log_external_response(
                "invocation",
                transaction_id,
                status="transport_error",
                capability=capability.capability,
                error=str(exc),
            )
            raise SelectFailed(
                capability.capability,
                NO_STATUS_CODE,
                classify_status_code(NO_STATUS_CODE),
                str(exc),
            ) from exc

        # Inside a guard, not after it: the port promises "an answer or
        # SelectFailed", and a 200 with an unreadable body would otherwise
        # raise KeyError/IndexError straight past the planner tool's handler
        # and abort the whole run, losing every other ask's answers. Same
        # treatment the discovery adapter gives its own mapper.
        try:
            return map_select_response(
                response.json(),
                provider_id=capability.provider_id,
                provider_name=capability.provider_name,
            )
        except _MALFORMED_RESPONSE as exc:
            raise SelectFailed(
                capability.capability,
                NO_STATUS_CODE,
                # A defect, never transient: the same body reads the same way,
                # so retrying would only delay the failure.
                FailureClass.DEFECT,
                f"malformed response: {exc!r}",
            ) from exc
