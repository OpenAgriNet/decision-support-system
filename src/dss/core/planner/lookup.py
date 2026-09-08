"""Finds the ProviderCapability the model picked, by resource_id.

The model chooses among an ask's candidates by resource_id — the only
unambiguous identifier when several candidates share the same @type.
"""

from __future__ import annotations

from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability


def find_capability(
    discovery: DiscoveryResult, *, ask_index: int, resource_id: str
) -> ProviderCapability | None:
    for capability in discovery.capabilities.get(ask_index, ()):
        if capability.resource_id == resource_id:
            return capability
    return None
