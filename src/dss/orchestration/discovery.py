"""Composition root for the discovery client adapter."""

from __future__ import annotations

from datetime import datetime
from functools import partial
from typing import Protocol

import httpx

from dss.adapters.discovery.client import HttpCapabilityDiscovery, SchemaContextSource
from dss.adapters.observability.tracing import open_span
from dss.core.intent.models import Intent
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.provider_discovery.service import (
    CapabilityIndexSource,
    discover_providers,
)
from dss.core.shared.models import UserTurn
from dss.ports.area_lookup import AreaLookup
from dss.ports.discovery import CapabilityDiscovery


def build_capability_discovery(
    client: httpx.AsyncClient,
    base_url: str,
    schema_pack_cache: SchemaContextSource,
) -> HttpCapabilityDiscovery:
    return HttpCapabilityDiscovery(
        client=client,
        base_url=base_url,
        schema_pack_cache=schema_pack_cache,
    )


class DiscoverProviders(Protocol):
    async def __call__(
        self, intent: Intent, turn: UserTurn, *, now: datetime
    ) -> DiscoveryResult: ...


def build_discover_providers(
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    area_lookup: AreaLookup,
    radius_m: int,
) -> DiscoverProviders:
    """Bakes in the built adapter, cache, area index, and configured radius, so
    callers only ever supply what changes per turn: intent, turn, and now.

    The area index is bound here, not per turn: it is a few hundred rows read
    once at startup and shared by every turn.

    The fan-out is wrapped in a span from out here because `core/` may not open
    one itself (ADR-0012). That puts the boundary at the whole fan-out rather
    than at each slot — the slot tasks are spawned inside the core service — so
    a single slow provider shows up as its own `dss.discover` span underneath
    rather than as a slot span.

    That span stays green even when a provider fails. An ask nobody could serve
    is a normal outcome, not a broken fan-out: the other asks still answer and
    the turn still succeeds. The failed call's own `dss.discover` child is the
    red one. What the fan-out reports instead is two counts, because span
    status has no value between OK and ERROR and "one of three failed" needs
    one — and a count can be filtered and graphed, which a colour cannot.
    """

    bound = partial(
        discover_providers,
        discovery=discovery,
        schema_pack_cache=schema_pack_cache,
        area_lookup=area_lookup,
        radius_m=radius_m,
    )

    async def discover(
        intent: Intent, turn: UserTurn, *, now: datetime
    ) -> DiscoveryResult:
        with open_span("dss.provider_discovery") as span:
            result = await bound(intent, turn, now=now)
            span.set_attribute("asks_queried", len(result.failures))
            span.set_attribute(
                "asks_failed", sum(1 for f in result.failures.values() if f)
            )
            return result

    return discover
