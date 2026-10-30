"""Builds /select, /support and /status requests and maps their responses.

Pure translation only — no business logic. The attributes are already fully
assembled by the caller; this module only wraps them in the network envelope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import uuid4

import anyio
import httpx

from dss.adapters.network import SelectContext
from dss.adapters.network.models import StatusContext, SupportContext
from dss.adapters.network_common import (
    NO_STATUS_CODE,
    classify_status_code,
    extract_source_reference,
)
from dss.adapters.observability.tracing import open_span
from dss.core.forms.models import ActionReply
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.network import NetworkTransactionID
from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
)

# Defined on the port, not here: a failed select is part of the contract, so
# a caller can catch it without importing this adapter. Re-exported because
# this module raises it.
from dss.ports.invocation import (
    ProviderCallFailedError,
    SelectFailedError,
    StatusFailedError,
    SupportFailedError,
)

__all__ = [
    "HttpCapabilityInvocation",
    "SelectFailedError",
    "StatusFailedError",
    "SupportFailedError",
    "build_select_request",
    "build_status_request",
    "build_support_request",
]

# A 200 whose body we cannot read. KeyError/IndexError for a missing key or
# an empty commitments list, TypeError for a wrong shape, ValueError because
# json.JSONDecodeError is one — a body that is not JSON at all.
_MALFORMED_RESPONSE = (KeyError, IndexError, TypeError, ValueError)

_DEFAULT_STATUS_DESCRIPTOR = {"code": "DRAFT", "name": "Draft"}
_DEFAULT_OFFER_ID = "offer:open-data"


def build_select_request(  # noqa: PLR0913
    capability: ProviderCapability,
    resource_attributes: dict,
    *,
    resource_id: str,
    sender_id: str,
    receiver_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
    status_descriptor: dict = _DEFAULT_STATUS_DESCRIPTOR,
    offer_id: str = _DEFAULT_OFFER_ID,
) -> dict[str, Any]:
    context = SelectContext(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": {
            "contract": {
                "commitments": [
                    {
                        "status": {"descriptor": status_descriptor},
                        "resources": [
                            {
                                "id": resource_id,
                                "resourceAttributes": resource_attributes,
                                "quantity": {"count": 1},
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


def build_support_request(  # noqa: PLR0913
    attributes: dict,
    *,
    sender_id: str,
    receiver_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
) -> dict[str, Any]:
    """On `support` there is no contract to hang the payload from: the pack
    names `channels` as its container, and the provider is inside the
    attributes, where the adapter routes by it."""

    context = SupportContext(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": {"support": {"channels": [attributes]}},
    }


def map_support_response(response: dict[str, Any]) -> dict[str, Any]:
    """The reply rides where the request did: the first support channel."""

    attributes = response["message"]["support"]["channels"][0]
    if not isinstance(attributes, dict):
        raise TypeError(
            f"support channel is {type(attributes).__name__}, not an object"
        )
    return attributes


def build_status_request(  # noqa: PLR0913
    attributes: dict,
    *,
    resource_id: str,
    contract_id: str,
    sender_id: str,
    receiver_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
) -> dict[str, Any]:
    """On `status` the case rides on a commitment: the pack names
    `commitmentAttributes` as its container. The one resource is the desk's
    thin pointer, the same id on every call; only the attributes differ."""

    context = StatusContext(
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": {
            "contract": {
                "id": contract_id,
                "commitments": [
                    {
                        "resources": [{"id": resource_id, "quantity": {"count": 1}}],
                        "commitmentAttributes": attributes,
                    }
                ],
            }
        },
    }


def map_status_response(response: dict[str, Any]) -> dict[str, Any]:
    """The case comes back where it was asked for: the first commitment."""

    commitment = response["message"]["contract"]["commitments"][0]
    attributes = commitment["commitmentAttributes"]
    if not isinstance(attributes, dict):
        raise TypeError(
            f"commitmentAttributes is {type(attributes).__name__}, not an object"
        )
    return attributes


def map_select_response(
    response: dict[str, Any], *, provider_id: str, provider_name: str
) -> list[DiscoveredAnswer]:
    """The provider may assign its own resource id in the response — distinct
    from the discovered id we named in the request. A search resource answers
    with one id per result found, not the id that was selected.

    Every resource goes into ``attributes["resources"]``, even when there is
    only one, so the model always reads the same shape. A forecast sends one
    resource per day, and the model needs all of them.

    Resources are grouped by the document they came from, one answer per
    document. A forecast's five days share a source and stay one answer. A
    knowledge search returning passages from two documents becomes two, because
    a single answer carries a single source: lumping them together made every
    passage inherit the first one's, and half the advice was then cited to a
    document it never came from.

    Resources from every commitment are read, and a response committing to
    nothing yields no answers — an empty 200 is a real answer shape, not a body
    we failed to parse.
    """

    grouped: dict[tuple[str | None, str | None, str | None], list[dict[str, Any]]] = {}
    for commitment in response["message"]["contract"]["commitments"]:
        for resource in commitment["resources"]:
            attributes = resource["resourceAttributes"]
            reference = extract_source_reference(attributes) or (None, None, None)
            grouped.setdefault(reference, []).append(resource)

    return [
        _to_answer(
            resources,
            provider_id=provider_id,
            provider_name=provider_name,
            reference=reference,
        )
        for reference, resources in grouped.items()
    ]


def _to_answer(
    resources: list[dict[str, Any]],
    *,
    provider_id: str,
    provider_name: str,
    reference: tuple[str | None, str | None, str | None],
) -> DiscoveredAnswer:
    source_id, source_name, source_url = reference
    first = resources[0]["resourceAttributes"]
    return DiscoveredAnswer(
        provider_id=provider_id,
        provider_name=provider_name,
        capability=first["@type"],
        resource_id=resources[0]["id"],
        attributes={
            "resources": [
                {"id": resource["id"], **resource["resourceAttributes"]}
                for resource in resources
            ]
        },
        # Each resource keeps its own validity in `attributes`.
        validity=None,
        source_id=source_id,
        source_name=source_name,
        source_url=source_url,
    )


class HttpCapabilityInvocation:
    """Implements CapabilityInvocation over the Network Adapter's /select.

    Owns the real HTTP call; request-building and response-mapping stay as
    the pure functions above so they're testable without a client at all.
    """

    def __init__(  # noqa: PLR0913
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
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> list[DiscoveredAnswer]:
        """Call /select, retrying a transient failure.

        ``resource_attributes`` holds tags. ``visibility.reveal`` swaps the real
        values in on the wire only; the logs, a failure's detail and the answer
        hold the tags. Without a map nothing is revealed, so a provider gets the tag.
        """

        return await self._call_with_retries(
            "dss.select",
            capability,
            partial(
                self._select_once,
                capability,
                resource_attributes,
                transaction_id,
                visibility,
            ),
            outcome_attributes=_select_outcome,
        )

    async def support(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        """Call /support, retrying a transient failure exactly as `select`
        does. The path is the Beckn convention; the pack's README never names
        it."""

        # One message id for the whole filing, kept across the retries: a
        # provider that filed but answered late sees the same message again,
        # the nearest thing to a duplicate key until the pack gives one.
        message_id = str(uuid4())
        return await self._call_with_retries(
            "dss.support",
            capability,
            partial(
                self._support_once,
                capability,
                attributes,
                transaction_id,
                visibility,
                message_id=message_id,
            ),
            outcome_attributes=_no_outcome_attributes,
        )

    async def status(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        """Call /status, retrying a transient failure exactly as `select`
        does. The path is the Beckn convention, as `/support` is."""

        # One message and one contract for the whole read, kept across the
        # retries, as a filing keeps its message id.
        message_id = str(uuid4())
        contract_id = str(uuid4())
        return await self._call_with_retries(
            "dss.status",
            capability,
            partial(
                self._status_once,
                capability,
                attributes,
                transaction_id,
                visibility,
                message_id=message_id,
                contract_id=contract_id,
            ),
            outcome_attributes=_no_outcome_attributes,
        )

    async def _call_with_retries[T](
        self,
        span_name: str,
        capability: ProviderCapability,
        once: Callable[[], Awaitable[T]],
        *,
        outcome_attributes: Callable[[T], Mapping[str, object]],
    ) -> T:
        """Only ``TRANSIENT`` failures are retried: a ``DEFECT`` (400/401/403)
        is a malformed request or bad credentials, so sending it again changes
        nothing and delays the failure the caller needs.

        Backoff doubles per attempt (0.5s, 1s, ...) rather than retrying at
        once, because ``429`` is also transient and hammering a rate-limited
        provider is what caused it. A provider that names its own time with
        `Retry-After` is not retried at all: the time is passed on, and the
        caller decides when.

        Two span levels, because the outer duration alone cannot tell one slow
        response from three failures with waits between them: the outer span
        is what reaching the provider cost in total, and one ``.attempt`` child
        per network call says how that total was spent.
        """

        with open_span(
            span_name,
            attributes={
                "provider_id": capability.provider_id,
                "capability": capability.capability,
            },
        ) as call:
            for attempt in range(1, self._attempts + 1):
                call.set_attribute("attempts", attempt)
                try:
                    with open_span(f"{span_name}.attempt") as span:
                        try:
                            result = await once()
                            for key, value in outcome_attributes(result).items():
                                call.set_attribute(key, value)
                            return result
                        except ProviderCallFailedError as failure:
                            # The code, never a detail — that may carry the
                            # provider's response body, which echoes the
                            # farmer's words back (§6.1). Without it every
                            # failed attempt reads the same, and a 503 and a
                            # 429 make identically shaped boxes.
                            span.set_attribute("http.status_code", failure.status_code)
                            raise
                except ProviderCallFailedError as failure:
                    last_chance = attempt == self._attempts
                    if (
                        failure.failure_class is not FailureClass.TRANSIENT
                        or failure.retry_after_seconds is not None
                        or last_chance
                    ):
                        call.set_attribute("failure_class", failure.failure_class.value)
                        raise
                    await anyio.sleep(self._backoff_seconds * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop either returns or raises")

    async def _select_once(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
        visibility: Visibility,
    ) -> list[DiscoveredAnswer]:
        request_body = build_select_request(
            capability,
            resource_attributes,
            resource_id=capability.resource_id,
            sender_id=self._sender_id,
            receiver_id=self._receiver_id,
            message_id=str(uuid4()),
            transaction_id=transaction_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        response = await self._post_once(
            "select",
            request_body,
            capability=capability,
            visibility=visibility,
            failed=_select_failed,
        )
        # Inside a guard, not after it: the port promises "an answer or
        # SelectFailedError", and a 200 with an unreadable body would otherwise
        # raise KeyError/IndexError straight past the planner tool's handler
        # and abort the whole run, losing every other ask's answers. Same
        # treatment the discovery adapter gives its own mapper.
        try:
            return map_select_response(
                visibility.conceal_body(response.json()),
                provider_id=capability.provider_id,
                provider_name=capability.provider_name,
            )
        except _MALFORMED_RESPONSE as exc:
            raise SelectFailedError(
                capability.capability,
                NO_STATUS_CODE,
                # A defect, never transient: the same body reads the same way,
                # so retrying would only delay the failure.
                FailureClass.DEFECT,
                f"malformed response: {exc!r}",
            ) from exc

    async def _support_once(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: str,
        visibility: Visibility,
        *,
        message_id: str,
    ) -> ActionReply:
        request_body = build_support_request(
            attributes,
            sender_id=self._sender_id,
            receiver_id=self._receiver_id,
            message_id=message_id,
            transaction_id=transaction_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        response = await self._post_once(
            "support",
            request_body,
            capability=capability,
            visibility=visibility,
            failed=_support_failed,
        )
        try:
            attributes_back = map_support_response(
                visibility.conceal_body(response.json())
            )
        except _MALFORMED_RESPONSE as exc:
            # A defect, never transient: the same body reads the same way.
            raise SupportFailedError(
                capability.capability, NO_STATUS_CODE, FailureClass.DEFECT
            ) from exc
        return ActionReply(attributes=attributes_back)

    async def _status_once(  # noqa: PLR0913
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: str,
        visibility: Visibility,
        *,
        message_id: str,
        contract_id: str,
    ) -> ActionReply:
        request_body = build_status_request(
            attributes,
            resource_id=capability.resource_id,
            contract_id=contract_id,
            sender_id=self._sender_id,
            receiver_id=self._receiver_id,
            message_id=message_id,
            transaction_id=transaction_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        response = await self._post_once(
            "status",
            request_body,
            capability=capability,
            visibility=visibility,
            failed=_status_failed,
        )
        try:
            attributes_back = map_status_response(
                visibility.conceal_body(response.json())
            )
        except _MALFORMED_RESPONSE as exc:
            # A defect, never transient: the same body reads the same way.
            raise StatusFailedError(
                capability.capability, NO_STATUS_CODE, FailureClass.DEFECT
            ) from exc
        return ActionReply(attributes=attributes_back)

    async def _post_once(
        self,
        action: str,
        request_body: dict,
        *,
        capability: ProviderCapability,
        visibility: Visibility,
        failed: _FailureBuilder,
    ) -> httpx.Response:
        """One POST to the action's endpoint, logged with tags on both sides.
        A non-2xx status or a transport error becomes the action's own
        error through ``failed``; the body is left to the action's mapper."""

        endpoint = f"{self._base_url}/{action}"
        transaction_id = request_body["context"]["transactionId"]
        log_external_request(
            "invocation",
            transaction_id,
            endpoint=endpoint,
            capability=capability.capability,
            # Named on every line because one ask can now call several
            # providers (ADR-0019), and a capability alone no longer says
            # which of them a body belongs to.
            provider_id=capability.provider_id,
            body=request_body,
        )
        try:
            # The one place a real value leaves the DSS (ADR-0015).
            response = await self._client.post(
                endpoint, json=visibility.reveal(request_body)
            )
            log_external_response(
                "invocation",
                transaction_id,
                status=response.status_code,
                capability=capability.capability,
                provider_id=capability.provider_id,
                body=visibility.conceal(response.text),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise failed(
                capability.capability,
                exc.response.status_code,
                exc.response,
                visibility.conceal(exc.response.text),
            ) from exc
        except httpx.HTTPError as exc:
            log_external_response(
                "invocation",
                transaction_id,
                status="transport_error",
                capability=capability.capability,
                provider_id=capability.provider_id,
                error=visibility.conceal(str(exc)),
            )
            raise failed(
                capability.capability,
                NO_STATUS_CODE,
                None,
                visibility.conceal(str(exc)),
            ) from exc
        return response


# The action's own error for a failed POST: the capability, the status code
# (`NO_STATUS_CODE` for a transport error), the response if there was one,
# and the concealed detail.
type _FailureBuilder = Callable[
    [str, int, httpx.Response | None, str], ProviderCallFailedError
]


def _select_failed(
    capability: str, status_code: int, response: httpx.Response | None, detail: str
) -> SelectFailedError:
    # The detail reaches the planner's prompt.
    return SelectFailedError(
        capability, status_code, classify_status_code(status_code), detail
    )


def _support_failed(
    capability: str, status_code: int, response: httpx.Response | None, detail: str
) -> SupportFailedError:
    return SupportFailedError(
        capability,
        status_code,
        classify_status_code(status_code),
        retry_after_seconds=(
            _retry_after_seconds(response) if response is not None else None
        ),
    )


def _status_failed(
    capability: str, status_code: int, response: httpx.Response | None, detail: str
) -> StatusFailedError:
    return StatusFailedError(
        capability,
        status_code,
        classify_status_code(status_code),
        retry_after_seconds=(
            _retry_after_seconds(response) if response is not None else None
        ),
    )


def _no_outcome_attributes(reply: ActionReply) -> Mapping[str, object]:
    return {}


def _select_outcome(answers: Sequence[DiscoveredAnswer]) -> Mapping[str, object]:
    # `answered`: a 200 with nothing in it looks healthy otherwise.
    # `answer_count`: how many documents one call drew on — a search
    # provider's passages may come from several.
    return {"answered": _has_data(answers), "answer_count": len(answers)}


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """The provider's own `Retry-After`, in seconds. The HTTP-date form is
    rare and not read: no number is better than a wrong one."""

    header = response.headers.get("Retry-After", "")
    return int(header) if header.isdigit() else None


def _has_data(answers: Sequence[DiscoveredAnswer]) -> bool:
    """Whether any resource carries anything beyond the JSON-LD envelope and
    its id.

    Empty lists count as nothing: valid, but they tell the farmer nothing.
    One answer with data is enough — the call was not wasted.
    """

    return any(
        value not in (None, "", [], {})
        for answer in answers
        for resource in answer.attributes["resources"]
        for key, value in resource.items()
        if not key.startswith("@") and key != "id"
    )
