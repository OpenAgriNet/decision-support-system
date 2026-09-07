"""The seam for the network's provider-invocation hop — implemented by an adapter.

Out of scope for Provider Discovery: the port is declared here so the type
exists to keep discovery's read-only barrier enforceable, but `select`/
`on_select` itself belongs to the Plan Executioner.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability


class CapabilityInvocation(Protocol):
    async def select(self, capability: ProviderCapability) -> DiscoveredAnswer: ...
