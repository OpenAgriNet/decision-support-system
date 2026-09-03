"""Builds /discover requests and maps on_discover responses.

Pure translation only — no business logic. A resource's own subjectCategories
matching or diverging from the index, expired validity, ranking, etc. are
core's job. This module never decides anything; it only reshapes data.
"""

from __future__ import annotations

from typing import Any

from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability

_ON_DEMAND = "OnDemand"


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
