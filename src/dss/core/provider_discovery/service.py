"""Behaviour for provider_discovery. Plain Python in, plain Python out."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
    CapabilityIndex,
    CapabilityUnresolved,
    CategoryMappingDiverged,
    Coverage,
    DiscoveredAsk,
    DiscoveredAsks,
    DiscoveryResult,
    NetworkSchemaType,
    ProviderQuery,
    ProviderQueryResult,
)
from dss.core.schema_packs.service import is_place_needed
from dss.core.shared.models import ProviderAction, UserTurn
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


def _action_type_of(ask: Ask) -> str:
    # An ask with a known action wants it done, however it is worded: "how do
    # I file a complaint" is advice by its words and a filing by its aim.
    if isinstance(ask.action, ProviderAction):
        return "Service"
    return _ACTION_TYPE_BY_INTERACTION[ask.interaction_type]


class CapabilityIndexSource(Protocol):
    def current(self) -> Mapping[tuple[str, str], tuple[str, ...]]: ...

    def place_bound_types(self) -> frozenset[str]: ...


def resolve_capability_type(
    subject_category: str,
    action_type: str,
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[tuple[str, ...], CapabilityUnresolved | None]:
    types = index.get((subject_category, action_type), ())
    if not types:
        return (), CapabilityUnresolved(subject_category, action_type)
    return types, None


def capability_types_for(
    ask: Ask, index: CapabilityIndex
) -> tuple[NetworkSchemaType, ...]:
    """The pack types discovery would search for this ask."""

    return index.get((ask.subject_categories.value, _action_type_of(ask)), ())


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
    place_bound_types: frozenset[str],
) -> ProviderQuery | CapabilityUnresolved:
    types, event = resolve_capability_type(
        ask.subject_categories.value, _action_type_of(ask), index
    )
    if (
        event is not None
        and ask.subject_categories not in _DISCOVERABLE_WITHOUT_CAPABILITY_TYPE
    ):
        return event
    return ProviderQuery(
        capabilities=types,
        subject_category=ask.subject_categories.value,
        languages=languages,
        coverage=coverage if is_place_needed(types, place_bound_types) else None,
    )


@dataclass(frozen=True)
class _NoPlaceToSearch:
    """The ask names a place we cannot use, and its search is filtered by
    place."""


_NO_PLACE_TO_SEARCH = _NoPlaceToSearch()


def _search_for(
    ask: Ask,
    languages: tuple[str, ...],
    radius_m: int,
    index: CapabilityIndex,
    place_bound_types: frozenset[NetworkSchemaType],
) -> ProviderQuery | CapabilityUnresolved | _NoPlaceToSearch:
    """What to search for this ask, or why it cannot be searched."""

    coverage = coverage_for_ask(ask, radius_m=radius_m)
    query = _query_for_ask(ask, languages, coverage, index, place_bound_types)
    if _lacks_a_needed_place(ask, query, place_bound_types):
        return _NO_PLACE_TO_SEARCH
    return query


def _lacks_a_needed_place(
    ask: Ask,
    query: ProviderQuery | CapabilityUnresolved,
    place_bound_types: frozenset[NetworkSchemaType],
) -> bool:
    # A safety net. Settlement asks for a needed place first. Without one,
    # the search has no place and could answer for some other place.
    if not isinstance(ask.place, AmbiguousPlace | UnresolvedPlace):
        return False
    return isinstance(query, CapabilityUnresolved) or is_place_needed(
        query.capabilities, place_bound_types
    )


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
    index: CapabilityIndex,
    place_bound_types: frozenset[NetworkSchemaType],
) -> AsksToBeDiscovered:
    """Coverage is computed per ask: each ask carries its own resolved place
    (or none), so a turn asking about two places issues two spatially
    different discover calls rather than one shared coordinate."""

    outcomes = {
        ask_index: _search_for(ask, languages, radius_m, index, place_bound_types)
        for ask_index, ask in enumerate(intent.asks)
    }
    return AsksToBeDiscovered(
        queries_by_ask={
            i: outcome
            for i, outcome in outcomes.items()
            if isinstance(outcome, ProviderQuery)
        },
        unsearchable_asks=frozenset(
            i
            for i, outcome in outcomes.items()
            if not isinstance(outcome, ProviderQuery)
        ),
        events=tuple(
            outcome
            for outcome in outcomes.values()
            if isinstance(outcome, CapabilityUnresolved)
        ),
    )


async def _send_queries(
    to_discover: AsksToBeDiscovered,
    discovery: CapabilityDiscovery,
    transaction_id: str,
) -> DiscoveredAsks:
    unique_queries = to_discover.find_unique_queries()
    replies: dict[ProviderQuery, ProviderQueryResult] = {}

    async def _send(query: ProviderQuery) -> None:
        replies[query] = await discovery.discover(query, transaction_id=transaction_id)

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
        intent, languages, radius_m, index, schema_pack_cache.place_bound_types()
    )
    discovered: DiscoveredAsks = await _send_queries(
        to_discover, discovery, turn.transaction_id
    )
    fresh: DiscoveredAsks = discovered.within_validity(now)
    events = [
        *to_discover.events,
        *discovered.build_expired_answer_events(now),
        *_detect_divergence(fresh, index),
        *fresh.build_outcome_events(),
    ]
    return fresh.build_result(events)
