"""The seam for the network's provider-invocation hop — implemented by an adapter.

Declared here so the type exists to keep discovery's read-only barrier
enforceable, even before anything calls it.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability


class CapabilityInvocation(Protocol):
    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer: ...
