"""Behaviour for provider_discovery. Plain Python in, plain Python out."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

import anyio

from dss.core.intent.models import Ask, Intent, InteractionType
from dss.core.provider_discovery.models import (
    AskDiscoveryFailed,
    AskUnservable,
    CapabilityUnresolved,
    CategoryMappingDiverged,
    Coverage,
    DiscoveredAnswer,
    DiscoveryEvent,
    DiscoveryFailure,
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
        capabilities=types,
        subject_category=ask.subject_categories.value,
        languages=languages,
        coverage=coverage,
    ), None


def _is_outside_validity(answer: DiscoveredAnswer, now: datetime) -> bool:
    """Whether ``now`` falls outside the answer's validity window.

    Both bounds, not just ``ends_at``. A provider publishing tomorrow's mandi
    price with ``startsAt`` in the future was otherwise served as today's
    answer — the worse half of the two, since a stale price at least was true
    once. Either bound may be absent, which means unbounded on that side.
    """

    validity = answer.validity
    if validity is None:
        return False
    if validity.starts_at is not None and now < validity.starts_at:
        return True
    return validity.ends_at is not None and now > validity.ends_at


def _had_fallback(
    answer: DiscoveredAnswer, capabilities: tuple[ProviderCapability, ...]
) -> bool:
    return any(
        capability.provider_id == answer.provider_id
        and capability.capability == answer.capability
        for capability in capabilities
    )


def _drop_expired_answers(
    answers: tuple[DiscoveredAnswer, ...],
    capabilities: tuple[ProviderCapability, ...],
    now: datetime,
) -> tuple[tuple[DiscoveredAnswer, ...], list[ExpiredAnswerDropped]]:
    expired, kept = (
        tuple(a for a in answers if _is_outside_validity(a, now)),
        tuple(a for a in answers if not _is_outside_validity(a, now)),
    )
    events = [
        ExpiredAnswerDropped(
            provider_id=answer.provider_id,
            capability=answer.capability,
            resource_id=answer.resource_id,
            had_fallback=_had_fallback(answer, capabilities),
        )
        for answer in expired
    ]
    return kept, events


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


def _build_queries(
    intent: Intent,
    languages: tuple[str, ...],
    coverage: Coverage | None,
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[
    dict[ProviderQuery, list[int]],
    dict[int, tuple[str, ...]],
    set[int],
    list[DiscoveryEvent],
]:
    queries_to_asks: dict[ProviderQuery, list[int]] = {}
    ask_capabilities: dict[int, tuple[str, ...]] = {}
    unresolved_asks: set[int] = set()
    events: list[DiscoveryEvent] = []
    for ask_index, ask in enumerate(intent.asks):
        query, event = _query_for_ask(ask, languages, coverage, index)
        if query is None:
            assert event is not None
            events.append(event)
            unresolved_asks.add(ask_index)
            continue
        queries_to_asks.setdefault(query, []).append(ask_index)
        ask_capabilities[ask_index] = query.capabilities
    return queries_to_asks, ask_capabilities, unresolved_asks, events


async def _run_queries(
    queries_to_asks: dict[ProviderQuery, list[int]],
    unresolved_asks: set[int],
    discovery: CapabilityDiscovery,
    transaction_id: str,
) -> tuple[
    dict[int, tuple[DiscoveredAnswer, ...]],
    dict[int, tuple[ProviderCapability, ...]],
    dict[int, tuple[DiscoveryFailure, ...]],
    list[DiscoveryEvent],
]:
    answers: dict[int, tuple[DiscoveredAnswer, ...]] = {i: () for i in unresolved_asks}
    capabilities: dict[int, tuple[ProviderCapability, ...]] = {
        i: () for i in unresolved_asks
    }
    failures: dict[int, tuple[DiscoveryFailure, ...]] = {i: () for i in unresolved_asks}
    events: list[DiscoveryEvent] = []

    # discover() never raises (failures are data), so every
    # query's results land independently: one query hitting a defect never
    # cancels or delays a sibling still in flight.
    query_results: list[DiscoveryResult | None] = [None] * len(queries_to_asks)

    async def _run(
        slot: int, query: ProviderQuery, ask_indices: tuple[int, ...]
    ) -> None:
        query_results[slot] = await discovery.discover(
            query, ask_indices, transaction_id=transaction_id
        )

    async with anyio.create_task_group() as task_group:
        for slot, (query, ask_indices) in enumerate(queries_to_asks.items()):
            task_group.start_soon(_run, slot, query, tuple(ask_indices))

    for result in query_results:
        assert result is not None
        answers.update(result.answers)
        capabilities.update(result.capabilities)
        failures.update(result.failures)
        events.extend(result.events)

    return answers, capabilities, failures, events


def _apply_expiry_filter(
    answers: dict[int, tuple[DiscoveredAnswer, ...]],
    capabilities: dict[int, tuple[ProviderCapability, ...]],
    now: datetime,
) -> list[ExpiredAnswerDropped]:
    events: list[ExpiredAnswerDropped] = []
    for ask_index in answers:
        kept_answers, expiry_events = _drop_expired_answers(
            answers[ask_index], capabilities.get(ask_index, ()), now
        )
        answers[ask_index] = kept_answers
        events.extend(expiry_events)
    return events


def _detect_divergence(
    answers: dict[int, tuple[DiscoveredAnswer, ...]],
    capabilities: dict[int, tuple[ProviderCapability, ...]],
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> list[CategoryMappingDiverged]:
    # Runs after the expiry filter: divergence is an alert to go fix a
    # provider's data, so raising it for an answer dropped in the same turn
    # would point an operator at a resource that never reached the user.
    all_answers = [answer for tup in answers.values() for answer in tup]
    all_capabilities = [cap for tup in capabilities.values() for cap in tup]
    return [
        event
        for answer in all_answers
        for event in _diverged_categories(
            answer.capability,
            tuple(answer.attributes.get("subjectCategories", ())),
            index,
        )
    ] + [
        event
        for capability in all_capabilities
        for event in _diverged_categories(
            capability.capability, capability.observed_categories, index
        )
    ]


def _ask_events(
    ask_index: int,
    ask_failures: tuple[DiscoveryFailure, ...],
    has_answer: bool,
    has_capability: bool,
    capabilities: tuple[str, ...],
) -> list[AskDiscoveryFailed | AskUnservable]:
    if ask_failures:
        return [
            AskDiscoveryFailed(
                ask_index=ask_index,
                capability=failure.capability,
                failure_class=failure.failure_class,
                status_code=failure.status_code,
            )
            for failure in ask_failures
        ]
    if not has_answer and not has_capability:
        return [AskUnservable(ask_index=ask_index, capabilities=capabilities)]
    return []


def _build_ask_events(
    ask_capabilities: dict[int, tuple[str, ...]],
    answers: dict[int, tuple[DiscoveredAnswer, ...]],
    capabilities: dict[int, tuple[ProviderCapability, ...]],
    failures: dict[int, tuple[DiscoveryFailure, ...]],
) -> list[AskDiscoveryFailed | AskUnservable]:
    return [
        event
        for ask_index, ask_types in ask_capabilities.items()
        for event in _ask_events(
            ask_index,
            failures.get(ask_index, ()),
            bool(answers.get(ask_index)),
            bool(capabilities.get(ask_index)),
            ask_types,
        )
    ]


async def discover_providers(
    intent: Intent,
    turn: UserTurn,
    *,
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
    now: datetime,
) -> DiscoveryResult:
    """Find who can serve each of ``intent``'s asks.

    Everything past ``turn`` is keyword-only on purpose. ``build_discover_providers``
    binds ``discovery``/``schema_pack_cache``/``radius_m`` by keyword and leaves
    the caller to pass ``now``; with these positional, a caller passing ``now``
    by position landed it in ``discovery``'s slot and collided with the bound
    value. Keyword-only makes that impossible to write.
    """

    languages = (turn.target_lang,)
    coverage = _coverage(turn, radius_m)
    index = schema_pack_cache.current()

    queries_to_asks, ask_capabilities, unresolved_asks, events = _build_queries(
        intent, languages, coverage, index
    )

    answers, capabilities, failures, run_events = await _run_queries(
        queries_to_asks, unresolved_asks, discovery, turn.transaction_id
    )
    events.extend(run_events)

    events.extend(_apply_expiry_filter(answers, capabilities, now))
    events.extend(_detect_divergence(answers, capabilities, index))
    events.extend(_build_ask_events(ask_capabilities, answers, capabilities, failures))

    return DiscoveryResult(
        answers=answers,
        capabilities=capabilities,
        failures=failures,
        events=tuple(events),
    )
