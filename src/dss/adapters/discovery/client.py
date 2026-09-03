"""Builds /discover requests and maps on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, ranking, etc. are
core's job. This module never decides anything; it only reshapes data.
"""

from __future__ import annotations

from typing import Any

from dss.core.provider_discovery.models import (
    DiscoveryResult,
    ProviderCapability,
    ProviderQuery,
)

_ON_DEMAND = "OnDemand"
_DISCOVER_VERSION = "2.0.0"


def _capabilities_from_catalog(catalog: dict[str, Any]) -> list[ProviderCapability]:
    provider = catalog["provider"]
    capabilities = []
    for resource in catalog["resources"]:
        attributes = resource["resourceAttributes"]
        if attributes["informationMode"] != _ON_DEMAND:
            continue
        capabilities.append(
            ProviderCapability(
                provider_id=provider["id"],
                provider_name=provider["descriptor"]["name"],
                capability=attributes["@type"],
                resource_id=resource["id"],
            )
        )
    return capabilities


def map_on_discover_response(
    response: dict[str, Any], ask_indices: tuple[int, ...]
) -> DiscoveryResult:
    capabilities: list[ProviderCapability] = []
    for catalog in response["message"]["catalogs"]:
        capabilities.extend(_capabilities_from_catalog(catalog))

    capabilities_by_ask = {index: tuple(capabilities) for index in ask_indices}

    return DiscoveryResult(
        answers={},
        capabilities=capabilities_by_ask,
        failures={},
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
