"""Behaviour for provider_discovery. Plain Python in, plain Python out."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

import anyio

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    Intent,
    InteractionType,
    ResolvedPlace,
    SubjectCategory,
    UnresolvedPlace,
)
from dss.core.provider_discovery.models import (
    AsksToBeDiscovered,
    CapabilityUnresolved,
    CategoryMappingDiverged,
    Coverage,
    DiscoveredAsk,
    DiscoveredAsks,
    DiscoveryResult,
    ProviderQuery,
    ProviderQueryResult,
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


def coverage_for_ask(ask: Ask, *, radius_m: int) -> Coverage | None:
    """Where this ask's discover call searches, or ``None`` when it has no
    place to search around.

    Resolution already happened — ``core.location.resolve_places`` ran before
    discovery and filled ``ask.place`` (or didn't). This only reads it: an
    ``AmbiguousPlace``/``UnresolvedPlace``/``None`` all mean no coordinate to
    search from, so all three yield no coverage.
    """

    if not isinstance(ask.place, ResolvedPlace):
        return None
    lon, lat = ask.place.geometry.coordinates
    return Coverage(lat=lat, lon=lon, radius_m=radius_m)


# Categories that are discovered on the category alone when the index resolves
# no @type. A scheme ask is otherwise dropped before the network sees it: no
# published pack declares `Scheme` in its examples, which is where the index
# gets its categories, so ("Scheme", *) has no entry and never will until one
# does (#52). The jsonpath filter matches `subjectCategories`, so the query is
# well-formed without a @type — only `schemaContext` goes unnamed.
_DISCOVERABLE_WITHOUT_CAPABILITY_TYPE = frozenset({SubjectCategory.SCHEME})


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
    if (
        not types
        and ask.subject_categories not in _DISCOVERABLE_WITHOUT_CAPABILITY_TYPE
    ):
        return None, event
    return ProviderQuery(
        capabilities=types,
        subject_category=ask.subject_categories.value,
        languages=languages,
        coverage=coverage,
    ), None


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


def _find_asks_to_discover(
    intent: Intent,
    languages: tuple[str, ...],
    radius_m: int,
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> AsksToBeDiscovered:
    """Coverage is computed per ask: each ask carries its own resolved place
    (or none), so a turn asking about two places issues two spatially
    different discover calls rather than one shared coordinate."""

    queries_by_ask: dict[int, ProviderQuery] = {}
    unsearchable_asks: set[int] = set()
    events: list[CapabilityUnresolved] = []
    for ask_index, ask in enumerate(intent.asks):
        # Nowhere to search: an unfiltered discover would bring back answers
        # for some other place. The place failure is reported downstream.
        if isinstance(ask.place, AmbiguousPlace | UnresolvedPlace):
            unsearchable_asks.add(ask_index)
            continue
        coverage = coverage_for_ask(ask, radius_m=radius_m)
        query, event = _query_for_ask(ask, languages, coverage, index)
        if query is None:
            assert event is not None  # noqa: S101 - type narrowing only
            events.append(event)
            unsearchable_asks.add(ask_index)
            continue
        queries_by_ask[ask_index] = query
    return AsksToBeDiscovered(
        queries_by_ask=queries_by_ask,
        unsearchable_asks=frozenset(unsearchable_asks),
        events=tuple(events),
    )


async def _send_queries(
    to_discover: AsksToBeDiscovered,
    discovery: CapabilityDiscovery,
    transaction_id: str,
    *,
    sender_id: str,
) -> DiscoveredAsks:
    unique_queries = to_discover.find_unique_queries()
    replies: dict[ProviderQuery, ProviderQueryResult] = {}

    async def _send(query: ProviderQuery) -> None:
        replies[query] = await discovery.discover(
            query, transaction_id=transaction_id, sender_id=sender_id
        )

    # discover() never raises (failures are data), so every
    # query's results land independently: one query hitting a defect never
    # cancels or delays a sibling still in flight.
    async with anyio.create_task_group() as task_group:
        for query in unique_queries:
            task_group.start_soon(_send, query)

    searched = (
        DiscoveredAsk(index=index, query=query, query_result=replies[query])
        for query in unique_queries
        for index in to_discover.find_asks_sharing(query)
    )
    return DiscoveredAsks(
        asks=tuple(sorted(searched, key=lambda ask: ask.index)),
        unsearchable_asks=to_discover.unsearchable_asks,
    )


def _detect_divergence(
    discovered: DiscoveredAsks, index: Mapping[tuple[str, str], tuple[str, ...]]
) -> list[CategoryMappingDiverged]:
    # Runs after the expiry filter: divergence is an alert to go fix a
    # provider's data, so raising it for an answer dropped in the same turn
    # would point an operator at a resource that never reached the user.
    query_results = [ask.query_result for ask in discovered.asks]
    return [
        event
        for query_result in query_results
        for answer in query_result.answers
        for event in _diverged_categories(
            answer.capability,
            tuple(answer.attributes.get("subjectCategories", ())),
            index,
        )
    ] + [
        event
        for query_result in query_results
        for capability in query_result.capabilities
        for event in _diverged_categories(
            capability.capability, capability.observed_categories, index
        )
    ]


async def discover_providers(  # noqa: PLR0913
    intent: Intent,
    turn: UserTurn,
    *,
    discovery: CapabilityDiscovery,
    schema_pack_cache: CapabilityIndexSource,
    radius_m: int,
    now: datetime,
    sender_id: str,
) -> DiscoveryResult:
    """Find who can serve each of ``intent``'s asks.

    Coverage is per ask (``ask.place``, resolved before this runs) rather than
    one shared coordinate for the turn — see ``_find_asks_to_discover``.

    Everything past ``turn`` is keyword-only on purpose. ``build_discover_providers``
    binds ``discovery``/``schema_pack_cache``/``radius_m`` by keyword and leaves
    the caller to pass ``now``; with these positional, a caller passing ``now``
    by position landed it in ``discovery``'s slot and collided with the bound
    value. Keyword-only makes that impossible to write.
    """

    languages = (str(turn.target_lang),)
    index = schema_pack_cache.current()
    to_discover: AsksToBeDiscovered = _find_asks_to_discover(
        intent, languages, radius_m, index
    )
    discovered: DiscoveredAsks = await _send_queries(
        to_discover, discovery, turn.transaction_id, sender_id=sender_id
    )
    fresh: DiscoveredAsks = discovered.within_validity(now)
    events = [
        *to_discover.events,
        *discovered.build_expired_answer_events(now),
        *_detect_divergence(fresh, index),
        *fresh.build_outcome_events(),
    ]
    return fresh.build_result(events)
