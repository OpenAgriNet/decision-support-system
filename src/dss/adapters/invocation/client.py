"""Builds /select, /init, /support and /status requests and maps their responses.

Pure translation only — no business logic. The attributes are already fully
assembled by the caller; this module only wraps them in the network envelope.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from dss.adapters.http_client import HttpClient, RetryPredicate
from dss.adapters.invocation.replies import (
    read_commitment_attributes,
    read_select_answers,
    read_support_channel,
)
from dss.adapters.network import SelectContext
from dss.adapters.network.models import InitContext, StatusContext, SupportContext
from dss.adapters.network_common import (
    NO_STATUS_CODE,
    classify_status_code,
)
from dss.adapters.observability.tracing import open_span
from dss.core.forms.models import ActionReply
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import ProviderAction
from dss.core.shared.network import NetworkTransactionID
from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
    log_failure,
)

# Defined on the port, not here: a failed select is part of the contract, so
# a caller can catch it without importing this adapter. Re-exported because
# this module raises it.
from dss.ports.invocation import (
    ProviderActionFailedError,
    ProviderCallFailedError,
    SelectFailedError,
)

if TYPE_CHECKING:
    from opentelemetry.trace import Span

__all__ = [
    "HttpCapabilityInvocation",
    "ProviderActionFailedError",
    "SelectFailedError",
    "build_init_request",
    "build_select_request",
    "build_status_request",
    "build_support_request",
]

_MALFORMED_RESPONSE = (json.JSONDecodeError, UnicodeDecodeError, ValidationError)

_DEFAULT_STATUS_DESCRIPTOR = {"code": "DRAFT", "name": "Draft"}
_DEFAULT_OFFER_ID = "offer:open-data"


def _build_commitment(resources: list[dict], **other_fields: object) -> dict[str, Any]:
    """The protocol requires a status on every commitment, on every action
    that carries a contract — even one, like `status`, with none to report."""

    return {
        "status": {"descriptor": _DEFAULT_STATUS_DESCRIPTOR},
        "resources": resources,
        **other_fields,
    }


def _build_offer(
    capability: ProviderCapability,
    *,
    resource_id: str,
    offer_id: str,
) -> dict[str, Any]:
    return {
        "id": offer_id,
        "resourceIds": [resource_id],
        "provider": {
            "id": capability.provider_id,
            "descriptor": {"name": capability.provider_name},
        },
    }


def build_select_request(  # noqa: PLR0913
    capability: ProviderCapability,
    resource_attributes: dict,
    *,
    resource_id: str,
    sender_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
    offer_id: str = _DEFAULT_OFFER_ID,
) -> dict[str, Any]:
    context = SelectContext(
        sender_id=sender_id,
        receiver_id=capability.bpp_id,
        bpp_uri=capability.bpp_uri,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": {
            "contract": {
                "commitments": [
                    _build_commitment(
                        [
                            {
                                "id": resource_id,
                                "resourceAttributes": resource_attributes,
                                "quantity": {"count": 1},
                            }
                        ],
                        offer=_build_offer(
                            capability, resource_id=resource_id, offer_id=offer_id
                        ),
                    )
                ]
            }
        },
    }


def build_support_request(  # noqa: PLR0913
    capability: ProviderCapability,
    attributes: dict,
    *,
    sender_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
) -> dict[str, Any]:
    """On `support` there is no contract to hang the payload from: the pack
    names `channels` as its container."""

    context = SupportContext(
        sender_id=sender_id,
        receiver_id=capability.bpp_id,
        bpp_uri=capability.bpp_uri,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": {"support": {"channels": [attributes]}},
    }


def build_status_request(  # noqa: PLR0913
    capability: ProviderCapability,
    attributes: dict,
    *,
    resource_id: str,
    contract_id: str,
    sender_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
) -> dict[str, Any]:
    """On `status` the case rides on a commitment: the pack names
    `commitmentAttributes` as its container. The one resource is the desk's
    thin pointer, the same id on every call; only the attributes differ."""

    context = StatusContext(
        sender_id=sender_id,
        receiver_id=capability.bpp_id,
        bpp_uri=capability.bpp_uri,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": _commitment_message(
            capability, attributes, resource_id=resource_id, contract_id=contract_id
        ),
    }


def build_init_request(  # noqa: PLR0913
    capability: ProviderCapability,
    attributes: dict,
    *,
    resource_id: str,
    contract_id: str,
    sender_id: str,
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
) -> dict[str, Any]:
    # The pack names `commitmentAttributes`; the contract and resource around it
    # are copied from `status`.
    context = InitContext(
        sender_id=sender_id,
        receiver_id=capability.bpp_id,
        bpp_uri=capability.bpp_uri,
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
    )
    return {
        "context": context.to_wire(),
        "message": _commitment_message(
            capability, attributes, resource_id=resource_id, contract_id=contract_id
        ),
    }


def _commitment_message(
    capability: ProviderCapability,
    attributes: dict,
    *,
    resource_id: str,
    contract_id: str,
) -> dict[str, Any]:
    return {
        "contract": {
            "id": contract_id,
            "commitments": [
                _build_commitment(
                    [{"id": resource_id, "quantity": {"count": 1}}],
                    commitmentAttributes=attributes,
                    offer=_build_offer(
                        capability,
                        resource_id=resource_id,
                        offer_id=_DEFAULT_OFFER_ID,
                    ),
                )
            ],
        }
    }


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
        attempts: int = 3,
        backoff_seconds: float = 0.5,
        *,
        network_id: str,
    ) -> None:
        self._http = HttpClient(
            client, base_url=base_url, backoff_seconds=backoff_seconds
        )
        self._base_url = base_url
        self._sender_id = sender_id
        # Logged, never sent: a provider call's envelope carries no network id.
        self._network_id = network_id
        self._retries = attempts - 1

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

        with _record_provider_call("dss.select", capability) as call:
            request_body = build_select_request(
                capability,
                resource_attributes,
                resource_id=capability.resource_id,
                sender_id=self._sender_id,
                message_id=str(uuid4()),
                transaction_id=transaction_id,
                timestamp=_now(),
            )
            try:
                response = await self._post(
                    "select",
                    request_body,
                    capability=capability,
                    visibility=visibility,
                    retries=self._retries,
                    # Select never reads `Retry-After`, so it retries a 429 that
                    # names one.
                    should_retry=_is_transient,
                )
            except httpx.HTTPError as error:
                raise _as_select_error(
                    capability, error, visibility=visibility
                ) from error
            # Inside a guard, not after it: the port promises "an answer or
            # SelectFailedError", and a 200 with an unreadable body would
            # otherwise raise a ValidationError straight past the planner
            # tool's handler and abort the whole run, losing every other ask's
            # answers. Same treatment the discovery adapter gives its own mapper.
            try:
                answers = read_select_answers(
                    visibility.conceal_body(response.json()),
                    provider_id=capability.provider_id,
                    provider_name=capability.provider_name,
                )
            except _MALFORMED_RESPONSE as exc:
                raise SelectFailedError(
                    capability.capability,
                    NO_STATUS_CODE,
                    # A defect, never transient: the same body reads the same
                    # way, so retrying would only delay the failure.
                    FailureClass.DEFECT,
                    f"malformed response: {exc!r}",
                ) from exc
            for key, value in _select_outcome(answers).items():
                call.set_attribute(key, value)
            return answers

    async def init(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        with _record_provider_call("dss.init", capability):
            request_body = build_init_request(
                capability,
                attributes,
                resource_id=capability.resource_id,
                contract_id=str(uuid4()),
                sender_id=self._sender_id,
                message_id=str(uuid4()),
                transaction_id=transaction_id,
                timestamp=_now(),
            )
            # Never retried: each try can text the farmer a new OTP.
            try:
                response = await self._post(
                    "init",
                    request_body,
                    capability=capability,
                    visibility=visibility,
                )
            except httpx.HTTPError as error:
                raise _as_action_error(
                    ProviderAction.INIT, capability, error
                ) from error
            try:
                attributes_back = read_commitment_attributes(
                    visibility.conceal_body(response.json())
                )
            except _MALFORMED_RESPONSE as exc:
                # A defect, never transient: the same body reads the same way.
                raise ProviderActionFailedError(
                    ProviderAction.INIT,
                    capability.capability,
                    NO_STATUS_CODE,
                    FailureClass.DEFECT,
                ) from exc
            return ActionReply(attributes=attributes_back)

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

        with _record_provider_call("dss.support", capability):
            # One message id for the whole filing, kept across the retries: a
            # provider that filed but answered late sees the same message
            # again, the nearest thing to a duplicate key until the pack gives one.
            request_body = build_support_request(
                capability,
                attributes,
                sender_id=self._sender_id,
                message_id=str(uuid4()),
                transaction_id=transaction_id,
                timestamp=_now(),
            )
            try:
                response = await self._post(
                    "support",
                    request_body,
                    capability=capability,
                    visibility=visibility,
                    retries=self._retries,
                    should_retry=_is_transient_and_no_retry_after,
                )
            except httpx.HTTPError as error:
                raise _as_action_error(
                    ProviderAction.SUPPORT, capability, error
                ) from error
            try:
                attributes_back = read_support_channel(
                    visibility.conceal_body(response.json())
                )
            except _MALFORMED_RESPONSE as exc:
                # A defect, never transient: the same body reads the same way.
                raise ProviderActionFailedError(
                    ProviderAction.SUPPORT,
                    capability.capability,
                    NO_STATUS_CODE,
                    FailureClass.DEFECT,
                ) from exc
            return ActionReply(attributes=attributes_back)

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

        with _record_provider_call("dss.status", capability):
            # One message and one contract for the whole read, kept across
            # the retries, as a filing keeps its message id.
            request_body = build_status_request(
                capability,
                attributes,
                resource_id=capability.resource_id,
                contract_id=str(uuid4()),
                sender_id=self._sender_id,
                message_id=str(uuid4()),
                transaction_id=transaction_id,
                timestamp=_now(),
            )
            try:
                response = await self._post(
                    "status",
                    request_body,
                    capability=capability,
                    visibility=visibility,
                    retries=self._retries,
                    should_retry=_is_transient_and_no_retry_after,
                )
            except httpx.HTTPError as error:
                raise _as_action_error(
                    ProviderAction.STATUS, capability, error
                ) from error
            try:
                attributes_back = read_commitment_attributes(
                    visibility.conceal_body(response.json())
                )
            except _MALFORMED_RESPONSE as exc:
                # A defect, never transient: the same body reads the same way.
                raise ProviderActionFailedError(
                    ProviderAction.STATUS,
                    capability.capability,
                    NO_STATUS_CODE,
                    FailureClass.DEFECT,
                ) from exc
            return ActionReply(attributes=attributes_back)

    async def _post(  # noqa: PLR0913
        self,
        action: str,
        request_body: dict,
        *,
        capability: ProviderCapability,
        visibility: Visibility,
        retries: int = 0,
        should_retry: RetryPredicate | None = None,
    ) -> httpx.Response:
        """One logged call to the action's endpoint, retried by ``HttpClient``.
        Raises the httpx error as it came; the caller makes its own error."""

        call_log = _CallLog(
            transaction_id=request_body["context"]["transactionId"],
            labels=(capability.capability, capability.provider_id),
            fields={
                "capability": capability.capability,
                # One ask can call several providers, so each line names its own.
                "provider_id": capability.provider_id,
                "message_id": request_body["context"]["messageId"],
                "network_id": self._network_id,
            },
        )
        log_external_request(
            "invocation",
            call_log.transaction_id,
            labels=call_log.labels,
            endpoint=f"{self._base_url}/{action}",
            body=request_body,
            **call_log.fields,
        )
        try:
            # The one place a real value leaves the DSS (ADR-0015).
            response = await self._http.post(
                action,
                json=visibility.reveal(request_body),
                retries=retries,
                should_retry=should_retry,
            )
        except httpx.HTTPError as error:
            _log_failed_response(call_log, error, visibility=visibility)
            raise
        log_external_response(
            "invocation",
            call_log.transaction_id,
            labels=call_log.labels,
            status=response.status_code,
            body=visibility.conceal(response.text),
            **call_log.fields,
        )
        return response


@dataclass(frozen=True)
class _CallLog:
    transaction_id: str
    labels: tuple[str, ...]
    fields: Mapping[str, str]


@contextmanager
def _record_provider_call(
    span_name: str, capability: ProviderCapability
) -> Iterator[Span]:
    """The outer span is what reaching the provider cost in total; one
    ``dss.http.post`` child per try says how that total was spent."""

    with open_span(
        span_name,
        attributes={
            "provider_id": capability.provider_id,
            "capability": capability.capability,
        },
    ) as call:
        try:
            yield call
        except ProviderCallFailedError as failure:
            call.set_attribute("failure_class", failure.failure_class.value)
            _log_given_up(capability, failure)
            raise


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_transient(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return (
            classify_status_code(error.response.status_code) is FailureClass.TRANSIENT
        )
    # No reply came: a timeout or a refused connection.
    return True


# The provider named its own time: it is passed up, and the caller decides when.
def _is_transient_and_no_retry_after(error: httpx.HTTPError) -> bool:
    has_retry_after = (
        isinstance(error, httpx.HTTPStatusError)
        and _retry_after_seconds(error.response) is not None
    )
    return _is_transient(error) and not has_retry_after


def _as_select_error(
    capability: ProviderCapability,
    error: httpx.HTTPError,
    *,
    visibility: Visibility,
) -> SelectFailedError:
    status_code = _status_code_of(error)
    # The detail reaches the planner's prompt.
    detail = (
        error.response.text if isinstance(error, httpx.HTTPStatusError) else str(error)
    )
    return SelectFailedError(
        capability.capability,
        status_code,
        classify_status_code(status_code),
        visibility.conceal(detail),
    )


def _as_action_error(
    action: ProviderAction,
    capability: ProviderCapability,
    error: httpx.HTTPError,
) -> ProviderActionFailedError:
    status_code = _status_code_of(error)
    return ProviderActionFailedError(
        action,
        capability.capability,
        status_code,
        classify_status_code(status_code),
        retry_after_seconds=(
            _retry_after_seconds(error.response)
            if isinstance(error, httpx.HTTPStatusError)
            else None
        ),
    )


def _status_code_of(error: httpx.HTTPError) -> int:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    return NO_STATUS_CODE


def _log_failed_response(
    call_log: _CallLog, error: httpx.HTTPError, *, visibility: Visibility
) -> None:
    if isinstance(error, httpx.HTTPStatusError):
        log_external_response(
            "invocation",
            call_log.transaction_id,
            labels=call_log.labels,
            status=error.response.status_code,
            body=visibility.conceal(error.response.text),
            **call_log.fields,
        )
        return
    log_external_response(
        "invocation",
        call_log.transaction_id,
        labels=call_log.labels,
        status="transport_error",
        error_type=type(error).__name__,
        error=visibility.conceal(str(error)),
        **call_log.fields,
    )


def _log_given_up(
    capability: ProviderCapability, failure: ProviderCallFailedError
) -> None:
    """The caller answers around this failure, so this line is all that says why."""

    log_failure(
        "invocation",
        failure,
        event="provider_call_failed",
        provider_id=capability.provider_id,
        capability=capability.capability,
        status_code=failure.status_code,
        failure_class=failure.failure_class.value,
    )


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
