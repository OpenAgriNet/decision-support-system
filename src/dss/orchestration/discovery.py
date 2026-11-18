"""Composition root for the discovery client adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from functools import partial
from typing import Protocol

import httpx

from dss.adapters.discovery.client import HttpCapabilityDiscovery, SchemaContextSource
from dss.adapters.observability.tracing import set_current_span_attributes
from dss.core.intent.models import Ask
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.provider_discovery.service import (
    CapabilityIndexSource,
    discover_providers,
)
from dss.core.provider_preferences.models import AskPriority
from dss.core.provider_preferences.service import prioritise
from dss.core.shared.models import UserTurn
from dss.observability.trace_log import log_event
from dss.ports.discovery import CapabilityDiscovery
from dss.ports.provider_preferences import ProviderPreferences


def build_capability_discovery(
    client: httpx.AsyncClient,
    base_url: str,
    schema_pack_cache: SchemaContextSource,
    *,
    sender_id: str,
    network_host_id: str,
) -> HttpCapabilityDiscovery:
    return HttpCapabilityDiscovery(
        client=client,
        base_url=base_url,
        schema_pack_cache=schema_pack_cache,
        sender_id=sender_id,
        network_host_id=network_host_id,
    )


class DiscoverProviders(Protocol):
    async def __call__(
        self, asks: Sequence[Ask], turn: UserTurn, *, now: datetime
    ) -> DiscoveryResult: ...


# The discovery adapter for one turn. Built per turn because who the DSS
# says it is on the network can depend on the turn's caller.
type DiscoveryFor = Callable[[UserTurn], CapabilityDiscovery]


def build_discover_providers(
    discovery_for: DiscoveryFor,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
    preferences: ProviderPreferences,
) -> DiscoverProviders:
    """Binds what does not change per turn, so callers pass only the asks,
    the turn and now.

    The sender's ranking is applied here, once, on the way out (ADR-0019):
    every later reader sees the ranked set and needs to know nothing about
    senders.

    The counts go on the discovery span `turn.py` already has open: `core/`
    may not open one (ADR-0012). Counts, not a red span: an ask nobody serves
    is a normal outcome, and "one of three failed" has no span status.
    """

    bound = partial(
        discover_providers,
        schema_pack_cache=schema_pack_cache,
        radius_m=radius_m,
    )

    async def discover(
        asks: Sequence[Ask], turn: UserTurn, *, now: datetime
    ) -> DiscoveryResult:
        result = await bound(asks, turn, now=now, discovery=discovery_for(turn))
        preference = await preferences.for_sender(
            turn.sender_id, transaction_id=turn.transaction_id
        )
        result = prioritise(result, preference=preference)
        applied = _find_applied_priorities(result)
        _log_rankings_applied(applied, turn=turn)
        set_current_span_attributes(
            # Includes asks that were never searched.
            asks_total=len(result.asks),
            asks_failed=sum(1 for ask in result.asks if ask.failures),
            sender_id=turn.sender_id or "",
            asks_ranked=len(applied),
            providers_dropped=sum(len(priority.dropped) for priority in applied),
        )
        return result

    return discover


def _find_applied_priorities(result: DiscoveryResult) -> tuple[AskPriority, ...]:
    priorities = (ask.priority for ask in result.asks)
    looked_up = (priority for priority in priorities if priority is not None)
    return tuple(priority for priority in looked_up if priority.applied)


def _log_rankings_applied(priorities: Sequence[AskPriority], *, turn: UserTurn) -> None:
    """Ids and counts only, never the farmer's words, so INFO is safe.
    `dropped` is why the line exists: a provider the ranking excluded shows
    nowhere else."""

    for priority in priorities:
        log_event(
            "provider_preferences",
            turn.transaction_id,
            event="ranking_applied",
            ask_id=priority.ask_id,
            capability=priority.capability,
            sender_id=priority.sender_id or "-",
            source=priority.source,
            ranked=_ids(priority.ranking),
            found=_ids(priority.kept),
            dropped=_ids(priority.dropped),
        )


def _ids(providers: tuple[str, ...]) -> str:
    """Provider ids as one greppable field. `-` rather than an empty value, so
    a reader can tell the field was logged and was empty."""

    return ",".join(providers) or "-"
