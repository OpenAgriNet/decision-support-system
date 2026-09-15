"""Builds /discover requests and maps on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, ranking, etc. are
core's job. This module never decides anything; it only reshapes data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx

from dss.adapters.network_common import (
    NO_STATUS_CODE,
    classify_status_code,
    extract_source_reference,
    extract_validity,
)
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryFailure,
    DiscoveryResult,
    FailureClass,
    ProviderCapability,
    ProviderQuery,
)
from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
)


def _failed_labels(query: ProviderQuery) -> tuple[str, ...]:
    """What a failure of this query is reported against.

    Normally the @types asked for. A category-only query (#52) has none, and
    an empty tuple would report no failure at all — a 500 would read as
    "nobody serves this", so the category stands in for the type.
    """

    return query.capabilities or (query.subject_category,)


def _failure_result(
    query: ProviderQuery,
    ask_indices: tuple[int, ...],
    status_code: int,
    detail: str | None,
) -> DiscoveryResult:
    failure_class = classify_status_code(status_code)
    failures = tuple(
        DiscoveryFailure(
            capability=capability,
            status_code=status_code,
            failure_class=failure_class,
            detail=detail,
        )
        for capability in _failed_labels(query)
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
            status_code=NO_STATUS_CODE,
            failure_class=FailureClass.DEFECT,
            detail=detail,
        )
        for capability in _failed_labels(query)
    )
    return DiscoveryResult(
        answers={index: () for index in ask_indices},
        capabilities={index: () for index in ask_indices},
        failures={index: failures for index in ask_indices},
        events=(),
    )


class SchemaContextSource(Protocol):
    def current_schema_context(self) -> dict[str, str]: ...


_ON_DEMAND = "OnDemand"
_DIRECT = "Direct"
_DISCOVER_VERSION = "2.0.0"


# Beckn-level fields every resource carries, whatever its pack. What is left
# after removing them is the pack's own advertised vocabulary — the codes and
# values the model needs and cannot invent.
#
# A skip-list rather than a read of the pack's own `discovery_fields`: that
# list lives in profile.json, which this adapter cannot reach without a new
# index threaded through two layers. These keys are Beckn-level and rarely
# change, while the advertised fields change often — so the rare failure is
# the one that needs a code edit.
_STRUCTURAL_ATTRIBUTES = frozenset(
    {
        "@type",
        "@context",
        "informationMode",
        "subjectCategories",
        "languages",
        "coverageAreas",
        "validity",
    }
)


def _advertised(attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in attributes.items()
        if key not in _STRUCTURAL_ATTRIBUTES
    }


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
                provider_code=provider["descriptor"].get("code"),
                advertised=_advertised(attributes),
            )
        )
    return capabilities


def _answers_from_catalog(catalog: dict[str, Any]) -> list[DiscoveredAnswer]:
    provider = catalog["provider"]
    answers = []
    for resource in catalog.get("resources", ()):
        attributes = resource["resourceAttributes"]
        if attributes.get("informationMode") != _DIRECT:
            continue
        source_id, source_name, source_url = extract_source_reference(attributes) or (
            None,
            None,
            None,
        )
        answers.append(
            DiscoveredAnswer(
                provider_id=provider["id"],
                provider_name=provider["descriptor"]["name"],
                capability=attributes["@type"],
                resource_id=resource["id"],
                attributes=attributes,
                validity=extract_validity(attributes),
                source_id=source_id,
                source_name=source_name,
                source_url=source_url,
            )
        )
    return answers


def map_discover_response(
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
    schema_context_index: dict[str, str],
) -> list[str]:
    """The pack's own ``@context`` URL, with the @type as a fragment.

    The base URL comes from the pack rather than configuration — it is the
    pack that states where its context lives. The ``#{capability}`` fragment
    is discover's own addition: it names which type in that context the query
    is about, and the real ``discover_request.json`` carries it.
    """

    return [
        f"{schema_context_index[capability]}#{capability}"
        for capability in capabilities
    ]


def _jsonpath_filter(subject_category: str) -> dict[str, str]:
    """Match resources by `subjectCategories`, not by `@type`.

    The envelope's `schemaContext` already names the resolved @type, so that is
    what pins the query to a specific resource type. Repeating it here only
    narrowed the match to providers that also publish `@type` on every
    resource, for no gain.
    """

    return {
        "type": "jsonpath",
        "expression": (
            "$.catalogs[*].resources[*] ? "
            f'(@.resourceAttributes.subjectCategories[*] == "{subject_category}")'
        ),
    }


def _spatial_filter(query: ProviderQuery) -> list[dict[str, Any]]:
    if query.coverage is None:
        return []
    return [
        {
            "op": "S_DWITHIN",
            # Where the *resource* applies, not where the provider's office is
            # (`provider.availableAt[*].geo`, which this used to target): a
            # provider based in one district can serve another, so filtering on
            # their own location excluded them wrongly.
            "targets": "$.catalogs[*].resources[*].resourceAttributes.coverageAreas[*]",
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
    schema_context_index: dict[str, str],
    message_id: str,
    transaction_id: str,
    timestamp: str,
) -> dict[str, Any]:
    intent: dict[str, Any] = {"filters": _jsonpath_filter(query.subject_category)}
    spatial = _spatial_filter(query)
    if spatial:
        intent["spatial"] = spatial

    context: dict[str, Any] = {
        "action": "discover",
        "version": _DISCOVER_VERSION,
        "messageId": message_id,
        "transactionId": transaction_id,
        "timestamp": timestamp,
    }
    # Omitted, not sent empty, when no @type resolved (#52): the contract takes
    # either the filter or schemaContext, and `[]` would assert that no schema
    # applies rather than that none was named.
    if query.capabilities:
        context["schemaContext"] = _schema_context_urls(
            query.capabilities, schema_context_index
        )

    return {"context": context, "message": {"intent": intent}}


class HttpCapabilityDiscovery:
    """Implements CapabilityDiscovery over the Network Adapter's /discover.

    Owns the real HTTP call; request-building and response-mapping stay as
    the pure functions above so they're testable without a client at all.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        schema_pack_cache: SchemaContextSource,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._schema_pack_cache = schema_pack_cache

    async def discover(
        self, query: ProviderQuery, ask_indices: tuple[int, ...], transaction_id: str
    ) -> DiscoveryResult:
        request_body = build_discover_request(
            query,
            schema_context_index=self._schema_pack_cache.current_schema_context(),
            message_id=str(uuid4()),
            transaction_id=transaction_id,
            timestamp=datetime.now(UTC).isoformat(),
        )
        log_external_request(
            "discovery",
            transaction_id,
            endpoint=f"{self._base_url}/discover",
            capabilities=",".join(query.capabilities),
            body=request_body,
        )
        try:
            response = await self._client.post(
                f"{self._base_url}/discover", json=request_body
            )
            log_external_response(
                "discovery",
                transaction_id,
                status=response.status_code,
                capabilities=",".join(query.capabilities),
                body=response.text,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return _failure_result(
                query, ask_indices, exc.response.status_code, exc.response.text
            )
        except httpx.HTTPError as exc:
            log_external_response(
                "discovery", transaction_id, status="transport_error", error=str(exc)
            )
            return _failure_result(query, ask_indices, NO_STATUS_CODE, str(exc))
        try:
            return map_discover_response(response.json(), ask_indices)
        except (KeyError, TypeError, ValueError) as exc:
            return _malformed_result(query, ask_indices, f"malformed response: {exc!r}")
