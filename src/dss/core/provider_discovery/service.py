"""Behaviour for provider_discovery. Plain Python in, plain Python out."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from dss.core.intent.models import Ask, Intent, InteractionType
from dss.core.provider_discovery.models import (
    CapabilityUnresolved,
    Coverage,
    DiscoveryResult,
    ProviderQuery,
)
from dss.core.shared.models import UserTurn
from dss.ports.discovery import CapabilityDiscovery

# The network's own horizontal axis is Knowledge/Service (network-specs'
# AgricultureResource.subjectCategories); the real Ask.interaction_type has a
# different, three-valued shape (advise/observe/act). This translation is
# deliberately isolated so either side can change without touching the other.
_ACTION_TYPE_BY_INTERACTION = {
    InteractionType.ADVISE: "Knowledge",
    InteractionType.OBSERVE: "Service",
    InteractionType.ACT: "Service",
}


class CapabilityIndexSource(Protocol):
    def current(self) -> Mapping[tuple[str, str], tuple[str, ...]]: ...


def resolve_capability_type(
    subject_category: str,
    action_type: str,
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[tuple[str, ...], CapabilityUnresolved | None]:
    types = index.get((subject_category, action_type), ())
    if not types:
        return (), CapabilityUnresolved(subject_category, action_type)
    return types, None


def _coverage(turn: UserTurn, radius_m: int) -> Coverage | None:
    if turn.location is None or turn.location.geometry is None:
        return None
    lon, lat = turn.location.geometry.coordinates
    return Coverage(lat=lat, lon=lon, radius_m=radius_m)


def _query_for_ask(
    ask: Ask,
    languages: tuple[str, ...],
    coverage: Coverage | None,
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[ProviderQuery | None, CapabilityUnresolved | None]:
    action_type = _ACTION_TYPE_BY_INTERACTION[ask.interaction_type]
    types, event = resolve_capability_type(
        ask.subject_categories.value, action_type, index
    )
    if not types:
        return None, event
    return ProviderQuery(
        capabilities=types, languages=languages, coverage=coverage
    ), None


async def discover_providers(
    intent: Intent,
    turn: UserTurn,
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
) -> DiscoveryResult:
    languages = (turn.target_lang,)
    coverage = _coverage(turn, radius_m)
    index = schema_pack_cache.current()

    queries_to_asks: dict[ProviderQuery, list[int]] = {}
    events = []
    unresolved_asks: set[int] = set()
    for ask_index, ask in enumerate(intent.asks):
        query, event = _query_for_ask(ask, languages, coverage, index)
        if query is None:
            assert event is not None
            events.append(event)
            unresolved_asks.add(ask_index)
            continue
        queries_to_asks.setdefault(query, []).append(ask_index)

    answers: dict[int, tuple] = {i: () for i in unresolved_asks}
    capabilities: dict[int, tuple] = {i: () for i in unresolved_asks}
    failures: dict[int, tuple] = {i: () for i in unresolved_asks}

    for query, ask_indices in queries_to_asks.items():
        result = await discovery.discover(query, tuple(ask_indices))
        answers.update(result.answers)
        capabilities.update(result.capabilities)
        failures.update(result.failures)
        events.extend(result.events)

    return DiscoveryResult(
        answers=answers,
        capabilities=capabilities,
        failures=failures,
        events=tuple(events),
    )
