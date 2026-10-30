"""The seam for the network's discovery hop — implemented by an adapter."""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_discovery.models import ProviderQuery, ProviderQueryResult
from dss.core.shared.network import NetworkTransactionID


class CapabilityDiscovery(Protocol):
    async def discover(
        self,
        query: ProviderQuery,
        transaction_id: NetworkTransactionID,
    ) -> ProviderQueryResult: ...
