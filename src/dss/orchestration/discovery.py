"""Composition root for the discovery client adapter."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Protocol

import httpx2

from dss.adapters.discovery.client import HttpCapabilityDiscovery, SchemaContextSource
from dss.core.intent.models import Intent
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.provider_discovery.service import (
    CapabilityIndexSource,
    discover_providers,
)
from dss.core.shared.models import UserTurn
from dss.ports.discovery import CapabilityDiscovery


def build_capability_discovery(
    client: httpx2.AsyncClient,
    base_url: str,
    schema_pack_cache: SchemaContextSource,
    schema_base_url: str,
) -> HttpCapabilityDiscovery:
    return HttpCapabilityDiscovery(
        client=client,
        base_url=base_url,
        schema_pack_cache=schema_pack_cache,
        schema_base_url=schema_base_url,
    )


class DiscoverProviders(Protocol):
    async def __call__(
        self, intent: Intent, turn: UserTurn, now: datetime
    ) -> DiscoveryResult: ...


def build_discover_providers(
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
) -> DiscoverProviders:
    """Bakes in the built adapter, cache, and configured radius, so callers
    only ever supply what changes per turn: intent, turn, and now.
    """
    return partial(
        discover_providers,
        discovery=discovery,
        schema_pack_cache=schema_pack_cache,
        radius_m=radius_m,
    )
