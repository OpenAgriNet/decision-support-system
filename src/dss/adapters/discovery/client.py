"""Builds /discover requests and maps on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, ranking, etc. are
core's job. This module never decides anything; it only reshapes data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx2

from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryFailure,
    DiscoveryResult,
    FailureClass,
    ProviderCapability,
    ProviderQuery,
    Validity,
)

_DEFECT_STATUS_CODES = {
    httpx2.codes.BAD_REQUEST,
    httpx2.codes.UNAUTHORIZED,
    httpx2.codes.FORBIDDEN,
}
_NO_STATUS_CODE = 0  # a pure network-level failure never had an HTTP response


def _classify_status_code(status_code: int) -> FailureClass:
    """BAD_REQUEST/UNAUTHORIZED/FORBIDDEN are our own bad request; every other
    status — 429, 500, and anything unlisted — is treated as transient/retry-worthy.
    """
    if status_code in _DEFECT_STATUS_CODES:
        return FailureClass.DEFECT
    return FailureClass.TRANSIENT


def _failure_result(
    query: ProviderQuery,
    ask_indices: tuple[int, ...],
    status_code: int,
    detail: str | None,
) -> DiscoveryResult:
    failure_class = _classify_status_code(status_code)
    failures = tuple(
        DiscoveryFailure(
            capability=capability,
            status_code=status_code,
            failure_class=failure_class,
            detail=detail,
        )
        for capability in query.capabilities
    )
    return DiscoveryResult(
        answers={index: () for index in ask_indices},
        capabilities={index: () for index in ask_indices},
        failures={index: failures for index in ask_indices},
        events=(),
    )


def _malformed_result(
    query: ProviderQuery, ask_indices: tuple[int, ...], detail: str
) -> DiscoveryResult:
    """A response we can't map is a defect on one side or the other — never
    retry-worthy — and it must not escape: discover() failing by exception
    would cancel every sibling query in the caller's task group.
    """
    failures = tuple(
        DiscoveryFailure(
            capability=capability,
            status_code=_NO_STATUS_CODE,
            failure_class=FailureClass.DEFECT,
            detail=detail,
        )
        for capability in query.capabilities
    )
    return DiscoveryResult(
        answers={index: () for index in ask_indices},
        capabilities={index: () for index in ask_indices},
        failures={index: failures for index in ask_indices},
        events=(),
    )


class SchemaContextSource(Protocol):
    def current_schema_context(self) -> dict[str, tuple[str, str]]: ...


_ON_DEMAND = "OnDemand"
_DIRECT = "Direct"
_DISCOVER_VERSION = "2.0.0"


def _capabilities_from_catalog(catalog: dict[str, Any]) -> list[ProviderCapability]:
    provider = catalog["provider"]
    capabilities = []
    for resource in catalog.get("resources", ()):
        attributes = resource["resourceAttributes"]
        if attributes.get("informationMode") != _ON_DEMAND:
            continue
        capabilities.append(
            ProviderCapability(
                provider_id=provider["id"],
                provider_name=provider["descriptor"]["name"],
                capability=attributes["@type"],
                resource_id=resource["id"],
                observed_categories=tuple(attributes.get("subjectCategories", ())),
            )
        )
    return capabilities


_BARE_DATE_LENGTH = len("YYYY-MM-DD")


def _to_utc(value: str, *, end_of_day: bool) -> datetime:
    """The spec allows a bare date (`2026-08-26`), which parses naive and at
    midnight. Core compares validity against a tz-aware now, so a zone has to
    be attached here. A bare endsAt means valid *through* that day, so it
    stretches to the day's end; a bare startsAt already means the day's start.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed
    if end_of_day and len(value) == _BARE_DATE_LENGTH:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed.replace(tzinfo=UTC)


def _extract_validity(attributes: dict[str, Any]) -> Validity | None:
    validity = attributes.get("validity")
    if validity is None:
        return None
    starts_at = validity.get("startsAt")
    ends_at = validity.get("endsAt")
    return Validity(
        starts_at=_to_utc(starts_at, end_of_day=False) if starts_at else None,
        ends_at=_to_utc(ends_at, end_of_day=True) if ends_at else None,
    )


def _answers_from_catalog(catalog: dict[str, Any]) -> list[DiscoveredAnswer]:
    provider = catalog["provider"]
    answers = []
    for resource in catalog.get("resources", ()):
        attributes = resource["resourceAttributes"]
        if attributes.get("informationMode") != _DIRECT:
            continue
        answers.append(
            DiscoveredAnswer(
                provider_id=provider["id"],
                provider_name=provider["descriptor"]["name"],
                capability=attributes["@type"],
                resource_id=resource["id"],
                attributes=attributes,
                validity=_extract_validity(attributes),
            )
        )
    return answers


def map_on_discover_response(
    response: dict[str, Any], ask_indices: tuple[int, ...]
) -> DiscoveryResult:
    capabilities: list[ProviderCapability] = []
    answers: list[DiscoveredAnswer] = []
    for catalog in response["message"]["catalogs"]:
        capabilities.extend(_capabilities_from_catalog(catalog))
        answers.extend(_answers_from_catalog(catalog))

    return DiscoveryResult(
        answers={index: tuple(answers) for index in ask_indices},
        capabilities={index: tuple(capabilities) for index in ask_indices},
        failures={index: () for index in ask_indices},
        events=(),
    )


def _schema_context_urls(
    capabilities: tuple[str, ...],
    schema_context_index: dict[str, tuple[str, str]],
    schema_base_url: str,
) -> list[str]:
    urls = []
    for capability in capabilities:
        pack_name, version = schema_context_index[capability]
        urls.append(
            f"{schema_base_url}/{pack_name}/{version}/context.jsonld#{capability}"
        )
    return urls


def _jsonpath_filter(capabilities: tuple[str, ...]) -> dict[str, str]:
    predicate = " || ".join(
        f'@.resourceAttributes."@type" == "{capability}"' for capability in capabilities
    )
    return {
        "type": "jsonpath",
        "expression": f"$.catalogs[*].resources[*] ? ({predicate})",
    }


def _spatial_filter(query: ProviderQuery) -> list[dict[str, Any]]:
    if query.coverage is None:
        return []
    return [
        {
            "op": "S_DWITHIN",
            "targets": "$.catalogs[*].provider.availableAt[*].geo",
            "geometry": {
                "type": "Point",
                "coordinates": [query.coverage.lon, query.coverage.lat],
            },
            "distanceMeters": query.coverage.radius_m,
            "quantifier": "ANY",
            "srid": "EPSG:4326",
        }
    ]


def build_discover_request(
    query: ProviderQuery,
    schema_context_index: dict[str, tuple[str, str]],
    schema_base_url: str,
    message_id: str,
    transaction_id: str,
    timestamp: str,
) -> dict[str, Any]:
    intent: dict[str, Any] = {"filters": _jsonpath_filter(query.capabilities)}
    spatial = _spatial_filter(query)
    if spatial:
        intent["spatial"] = spatial

    return {
        "context": {
            "action": "discover",
            "version": _DISCOVER_VERSION,
            "messageId": message_id,
            "transactionId": transaction_id,
            "timestamp": timestamp,
            "schemaContext": _schema_context_urls(
                query.capabilities, schema_context_index, schema_base_url
            ),
        },
        "message": {"intent": intent},
    }


class HttpCapabilityDiscovery:
    """Implements CapabilityDiscovery over the Network Adapter's /discover.

    Owns the real HTTP call; request-building and response-mapping stay as
    the pure functions above so they're testable without a client at all.
    """

    def __init__(
        self,
        client: httpx2.AsyncClient,
        base_url: str,
        schema_pack_cache: SchemaContextSource,
        schema_base_url: str,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._schema_pack_cache = schema_pack_cache
        self._schema_base_url = schema_base_url

    async def discover(
        self, query: ProviderQuery, ask_indices: tuple[int, ...], transaction_id: str
    ) -> DiscoveryResult:
        request_body = build_discover_request(
            query,
            schema_context_index=self._schema_pack_cache.current_schema_context(),
            schema_base_url=self._schema_base_url,
            message_id=str(uuid4()),
            transaction_id=transaction_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/discover", json=request_body
            )
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            return _failure_result(
                query, ask_indices, exc.response.status_code, exc.response.text
            )
        except httpx2.HTTPError as exc:
            return _failure_result(query, ask_indices, _NO_STATUS_CODE, str(exc))
        try:
            return map_on_discover_response(response.json(), ask_indices)
        except (KeyError, TypeError, ValueError) as exc:
            return _malformed_result(query, ask_indices, f"malformed response: {exc!r}")
