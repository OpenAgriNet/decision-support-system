"""Builds /discover requests and maps on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, ranking, etc. are
core's job. This module never decides anything; it only reshapes data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx
from opentelemetry.trace import Status, StatusCode

from dss.adapters.network import DiscoverContext, NetworkSchemaContext
from dss.adapters.network_common import (
    NO_STATUS_CODE,
    classify_status_code,
    extract_source_reference,
    extract_validity,
)
from dss.adapters.observability.tracing import open_span
from dss.core.intent.models import SubjectCategory
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryFailure,
    FailureClass,
    ProviderCapability,
    ProviderQuery,
    ProviderQueryResult,
)
from dss.core.shared.network import NetworkTransactionID
from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
)

logger = logging.getLogger(__name__)


def _failed_labels(query: ProviderQuery) -> tuple[str, ...]:
    """What a failure of this query is reported against.

    Normally the @types asked for. A category-only query (#52) has none, and
    an empty tuple would report no failure at all — a 500 would read as
    "nobody serves this", so the category stands in for the type.
    """

    return query.capabilities or (query.subject_category,)


def _failure_result(
    query: ProviderQuery, status_code: int, detail: str | None
) -> ProviderQueryResult:
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
    return ProviderQueryResult(answers=(), capabilities=(), failures=failures)


def _malformed_result(query: ProviderQuery, detail: str) -> ProviderQueryResult:
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
    return ProviderQueryResult(answers=(), capabilities=(), failures=failures)


class SchemaContextSource(Protocol):
    def current_schema_context(self) -> dict[str, str]: ...


_ON_DEMAND = "OnDemand"
_DIRECT = "Direct"

# Envelope-level fields every resource carries, whatever its pack. What is left
# after removing them is the pack's own advertised vocabulary — the codes and
# values the model needs and cannot invent.
#
# A skip-list rather than a read of the pack's own `discovery_fields`: that
# list lives in profile.json, which this adapter cannot reach without a new
# index threaded through two layers. These keys are envelope-level and rarely
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
    return [
        _to_capability(catalog, resource)
        for resource in _resources_in_mode(catalog, _ON_DEMAND)
    ]


def _answers_from_catalog(catalog: dict[str, Any]) -> list[DiscoveredAnswer]:
    return [
        _to_answer(catalog, resource)
        for resource in _resources_in_mode(catalog, _DIRECT)
    ]


def _resources_in_mode(catalog: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    return [
        resource
        for resource in catalog.get("resources", ())
        if resource["resourceAttributes"].get("informationMode") == mode
    ]


def _to_capability(
    catalog: dict[str, Any], resource: dict[str, Any]
) -> ProviderCapability:
    provider = catalog["provider"]
    attributes = resource["resourceAttributes"]
    return ProviderCapability(
        provider_id=provider["id"],
        provider_name=provider["descriptor"]["name"],
        capability=attributes["@type"],
        resource_id=resource["id"],
        bpp_id=catalog["bppId"],
        bpp_uri=catalog["bppUri"],
        observed_categories=tuple(attributes.get("subjectCategories", ())),
        provider_code=provider["descriptor"].get("code"),
        advertised=_advertised(attributes),
    )


def _to_answer(catalog: dict[str, Any], resource: dict[str, Any]) -> DiscoveredAnswer:
    provider = catalog["provider"]
    attributes = resource["resourceAttributes"]
    source_id, source_name, source_url = extract_source_reference(attributes) or (
        None,
        None,
        None,
    )
    return DiscoveredAnswer(
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


# A transaction is addressed to the catalog's BPP (`receiverId`, `bppUri`), so
# a catalog that names none cannot be called and is dropped whole.
_BPP_FIELDS = ("bppId", "bppUri")


def map_discover_response(response: dict[str, Any]) -> ProviderQueryResult:
    catalogs = response["message"]["catalogs"]
    addressable = [c for c in catalogs if _has_bpp(c)]
    unaddressable = [c for c in catalogs if not _has_bpp(c)]
    return ProviderQueryResult(
        answers=tuple(a for c in addressable for a in _answers_from_catalog(c)),
        capabilities=tuple(
            cap for c in addressable for cap in _capabilities_from_catalog(c)
        ),
        failures=tuple(f for c in unaddressable for f in _reject_catalog(c)),
    )


def _network_fields(request_body: dict[str, Any]) -> dict[str, str]:
    """The network message id and network id this call went out with."""

    context = request_body["context"]
    return {"message_id": context["messageId"], "network_id": context["networkId"]}


def _provider_ids_in(query_result: ProviderQueryResult) -> list[str]:
    """Every provider that answered, offered a capability or failed, once each."""

    return sorted(
        {c.provider_id for c in query_result.capabilities}
        | {a.provider_id for a in query_result.answers}
        | {f.provider_id for f in query_result.failures if f.provider_id}
    )


def _has_bpp(catalog: dict[str, Any]) -> bool:
    return all(catalog.get(field) for field in _BPP_FIELDS)


def _reject_catalog(catalog: dict[str, Any]) -> list[DiscoveryFailure]:
    """One failure per @type the catalog offered, so the gap is reported
    against what the ask will now go without."""

    provider_id = catalog["provider"]["id"]
    missing = next(field for field in _BPP_FIELDS if not catalog.get(field))
    detail = f"catalog {catalog.get('id')} has no {missing}"
    logger.warning(
        "catalog dropped", extra={"provider_id": provider_id, "reason": detail}
    )
    return [
        DiscoveryFailure(
            capability=capability,
            status_code=NO_STATUS_CODE,
            failure_class=FailureClass.DEFECT,
            provider_id=provider_id,
            detail=detail,
        )
        for capability in _offered_types(catalog)
    ]


def _offered_types(catalog: dict[str, Any]) -> tuple[str, ...]:
    """The distinct @types the catalog's resources carry, in catalog order."""

    types = (
        resource["resourceAttributes"].get("@type")
        for resource in catalog.get("resources", ())
    )
    return tuple(dict.fromkeys(t for t in types if t))


def _jsonpath_filter(subject_category: str) -> dict[str, str]:
    """Match resources by `subjectCategories`, not by `@type`.

    The envelope's `schemaContext` already names the resolved @type, so that is
    what pins the query to a specific resource type. Repeating it here only
    narrowed the match to providers that also publish `@type` on every
    resource, for no gain.
    """

    if subject_category == SubjectCategory.GRIEVANCE:
        return {
            "type": "jsonpath",
            "expression": (
                "$.catalogs[*].resources[*] "
                '? (@.resourceAttributes."@type" == "openagrinet:Grievance" && '
                '@.resourceAttributes.informationMode == "OnDemand")'
            ),
        }

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


def build_discover_request(  # noqa: PLR0913
    query: ProviderQuery,
    schema_context_index: dict[str, str],
    message_id: str,
    transaction_id: NetworkTransactionID,
    timestamp: str,
    *,
    sender_id: str,
    network_host_id: str,
) -> dict[str, Any]:
    intent: dict[str, Any] = {"filters": _jsonpath_filter(query.subject_category)}
    spatial = _spatial_filter(query)
    if spatial:
        intent["spatial"] = spatial

    context = DiscoverContext(
        message_id=message_id,
        transaction_id=transaction_id,
        timestamp=timestamp,
        sender_id=sender_id,
        receiver_id=network_host_id,
        network_id=network_host_id,
        # `None` when no @type resolved, which the envelope then omits rather
        # than sending empty (#52).
        schema_context=NetworkSchemaContext.for_types(
            query.capabilities, schema_context_index
        ),
    )

    return {"context": context.to_wire(), "message": {"intent": intent}}


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
        *,
        sender_id: str,
        network_host_id: str,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._schema_pack_cache = schema_pack_cache
        self._sender_id = sender_id
        self._network_host_id = network_host_id

    async def discover(
        self, query: ProviderQuery, transaction_id: NetworkTransactionID
    ) -> ProviderQueryResult:
        """One query to one provider. Never raises — see `_malformed_result`.

        The span covers building the request as well as sending it. Building it
        can fail: `_schema_context_urls` subscripts the schema-context index
        unguarded, and a capability can be in the capability index and not that
        one — a skipped pack leaves exactly that. Raising there would escape
        into the caller's task group and cancel every sibling query.

        It also keeps this call's two log lines under one span, so the
        `event=request` line naming what was sent and the `event=response` line
        carrying the outcome are found by the same `span_id`.

        The span has to be told when this fails, precisely because nothing is
        raised: left alone it would exit green, and a provider that timed out
        would be indistinguishable from one that answered.

        Three attributes, each answering something nothing else does: which
        capability this call was for, and whether the provider served anything.
        Not the status code — a failure already carries it in the span status,
        and a success is always 200. Not the bodies either: they hold the
        farmer's query verbatim, which §6.1 does not permit on a trace, so they
        stay on the DEBUG log lines and are found by this span's id.
        """

        with open_span(
            "dss.discover",
            attributes={"capabilities": ",".join(_failed_labels(query))},
        ) as span:
            try:
                request_body = build_discover_request(
                    query,
                    schema_context_index=(
                        self._schema_pack_cache.current_schema_context()
                    ),
                    message_id=str(uuid4()),
                    transaction_id=transaction_id,
                    timestamp=datetime.now(UTC).isoformat(),
                    sender_id=self._sender_id,
                    network_host_id=self._network_host_id,
                )
            except (KeyError, TypeError, ValueError) as exc:
                span.set_status(Status(StatusCode.ERROR, f"unbuildable: {exc!r}"))
                return _malformed_result(query, f"unbuildable request: {exc!r}")
            network_fields = {
                **_network_fields(request_body),
                "capabilities": ",".join(query.capabilities),
            }
            log_external_request(
                "discovery",
                transaction_id,
                endpoint=f"{self._base_url}/discover",
                body=request_body,
                **network_fields,
            )
            try:
                response = await self._client.post(
                    f"{self._base_url}/discover", json=request_body
                )
            except httpx.HTTPError as exc:
                log_external_response(
                    "discovery",
                    transaction_id,
                    status="transport_error",
                    error_type=type(exc).__name__,
                    **network_fields,
                )
                span.set_status(Status(StatusCode.ERROR, f"transport: {exc}"))
                return _failure_result(query, NO_STATUS_CODE, str(exc))
            if response.is_error:
                log_external_response(
                    "discovery",
                    transaction_id,
                    status=response.status_code,
                    body=response.text,
                    **network_fields,
                )
                span.set_status(
                    Status(StatusCode.ERROR, f"HTTP {response.status_code}")
                )
                return _failure_result(query, response.status_code, response.text)
            try:
                query_result = map_discover_response(response.json())
            except (KeyError, TypeError, ValueError) as exc:
                log_external_response(
                    "discovery",
                    transaction_id,
                    status="malformed",
                    error_type=type(exc).__name__,
                    body=response.text,
                    **network_fields,
                )
                span.set_status(Status(StatusCode.ERROR, f"malformed: {exc!r}"))
                return _malformed_result(query, f"malformed response: {exc!r}")
            provider_ids = _provider_ids_in(query_result)
            log_external_response(
                "discovery",
                transaction_id,
                status=response.status_code,
                provider_ids=provider_ids,
                provider_count=len(provider_ids),
                body=response.text,
                **network_fields,
            )
            # A green span that found nobody and one that found three providers
            # are otherwise identical. The catalogs themselves stay off the
            # span — they carry the provider's own data.
            span.set_attribute("capability_count", len(query_result.capabilities))
            span.set_attribute("answer_count", len(query_result.answers))
            # Ids only, never content. Catalog answers get no /select span,
            # so this is the only place they show up.
            span.set_attribute(
                "offered_provider_ids",
                sorted({c.provider_id for c in query_result.capabilities}),
            )
            span.set_attribute(
                "answered_provider_ids",
                sorted({a.provider_id for a in query_result.answers}),
            )
            return query_result
