"""Behaviour for provider_discovery. Plain Python in, plain Python out."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

import anyio

from dss.core.intent.models import Ask, Intent, InteractionType
from dss.core.provider_discovery.models import (
    CapabilityUnresolved,
    CategoryMappingDiverged,
    Coverage,
    DiscoveredAnswer,
    DiscoveryResult,
    ExpiredAnswerDropped,
    ProviderCapability,
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


def _is_expired(answer: DiscoveredAnswer, now: datetime) -> bool:
    if answer.validity is None or answer.validity.ends_at is None:
        return False
    return now > answer.validity.ends_at


def _drop_expired_answers(
    answers: tuple[DiscoveredAnswer, ...],
    capabilities: tuple[ProviderCapability, ...],
    now: datetime,
) -> tuple[tuple[DiscoveredAnswer, ...], list[ExpiredAnswerDropped]]:
    kept = []
    events = []
    for answer in answers:
        if not _is_expired(answer, now):
            kept.append(answer)
            continue
        had_fallback = any(
            capability.provider_id == answer.provider_id
            and capability.capability == answer.capability
            for capability in capabilities
        )
        events.append(
            ExpiredAnswerDropped(
                provider_id=answer.provider_id,
                capability=answer.capability,
                resource_id=answer.resource_id,
                had_fallback=had_fallback,
            )
        )
    return tuple(kept), events


def _expected_categories(
    capability: str, index: Mapping[tuple[str, str], tuple[str, ...]]
) -> set[str]:
    return {
        subject_category
        for (subject_category, _action_type), types in index.items()
        if capability in types
    }


def _diverged_categories(
    capability: str,
    observed_categories: tuple[str, ...],
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> list[CategoryMappingDiverged]:
    expected = _expected_categories(capability, index)
    return [
        CategoryMappingDiverged(capability=capability, observed_category=category)
        for category in observed_categories
        if category not in expected
    ]


async def discover_providers(
    intent: Intent,
    turn: UserTurn,
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
    now: datetime,
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

    # discover() never raises (failures are data), so every
    # query's results land independently: one query hitting a defect never
    # cancels or delays a sibling still in flight.
    query_results: list[DiscoveryResult | None] = [None] * len(queries_to_asks)

    async def _run(
        slot: int, query: ProviderQuery, ask_indices: tuple[int, ...]
    ) -> None:
        query_results[slot] = await discovery.discover(query, ask_indices)

    async with anyio.create_task_group() as task_group:
        for slot, (query, ask_indices) in enumerate(queries_to_asks.items()):
            task_group.start_soon(_run, slot, query, tuple(ask_indices))

    for result in query_results:
        assert result is not None
        answers.update(result.answers)
        capabilities.update(result.capabilities)
        failures.update(result.failures)
        events.extend(result.events)

    for answer_tuple in answers.values():
        for answer in answer_tuple:
            observed = tuple(answer.attributes.get("subjectCategories", ()))
            events.extend(_diverged_categories(answer.capability, observed, index))
    for capability_tuple in capabilities.values():
        for capability in capability_tuple:
            events.extend(
                _diverged_categories(
                    capability.capability, capability.observed_categories, index
                )
            )

    for ask_index in answers:
        kept_answers, expiry_events = _drop_expired_answers(
            answers[ask_index], capabilities.get(ask_index, ()), now
        )
        answers[ask_index] = kept_answers
        events.extend(expiry_events)

    return DiscoveryResult(
        answers=answers,
        capabilities=capabilities,
        failures=failures,
        events=tuple(events),
    )
