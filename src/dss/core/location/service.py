"""Resolves each ask's place from words to a `ResolvedPlace`.

The only place an `Ask` is built from a `ClassifiedAsk` — see
`core/intent/service.py`'s docstring. Precedence, one ask at a time:

    place named (this turn, or carried from earlier)
      > device geometry
      > client-asserted area
      > place another ask in this turn already resolved
      > nothing

Device geometry, sent this turn with the farmer's location consent, is a
fresher signal than an asserted area the platform is only repeating from an
earlier turn — so it outranks that repeat, though it still loses to whatever
the farmer actually said.
"""

from __future__ import annotations

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    ClassifiedAsk,
    Intent,
    IntentClassification,
    PlaceSource,
    ResolvedPlace,
    UnresolvedPlace,
)
from dss.core.shared.models import Location, UserTurn
from dss.ports.area_lookup import AreaLookup, AreaMatch

Place = ResolvedPlace | AmbiguousPlace | UnresolvedPlace | None


def _from_match(match: AreaMatch, source: PlaceSource) -> ResolvedPlace:
    return ResolvedPlace(
        name=match.name,
        within=match.within,
        geometry=match.geometry,
        source=source,
    )


def _drop_nested(matches: list[AreaMatch]) -> list[AreaMatch]:
    """A match inside another match of the same name — Nashik block inside
    Nashik district — adds no real choice: the larger place covers it."""

    return [
        match
        for match in matches
        if not any(match.within == (*outer.within, outer.name) for outer in matches)
    ]


def _resolve_named(
    name: str, lookup: AreaLookup, region: str | None, source: PlaceSource
) -> Place:
    """A place the farmer actually said, this turn or earlier — the one case
    that can fail loud (`AmbiguousPlace`/`UnresolvedPlace`) rather than fall
    through."""

    # "Rampur, Himachal Pradesh" is a line we listed and the farmer picked:
    # the name, then a place that contains it. No area name holds a comma.
    lookup_name, comma, part = name.rpartition(",")
    if not comma:
        lookup_name, part = name, ""
    matches = _drop_nested(lookup.resolve(lookup_name.strip()))
    part = part.strip().casefold()
    inside = [m for m in matches if part in {w.casefold() for w in m.within}]
    matches = inside or matches
    # The region is where the farmer is, not what they asked about: it only
    # breaks a tie between same-name places, never hides the one they named.
    if len(matches) > 1 and region is not None:
        matches = [m for m in matches if m.region == region] or matches
    if len(matches) == 1:
        return _from_match(matches[0], source)
    if len(matches) > 1:
        return AmbiguousPlace(unresolved_name=name, candidates=tuple(matches))
    return UnresolvedPlace(unresolved_name=name)


def _resolve_from_location(
    location: Location | None, lookup: AreaLookup
) -> ResolvedPlace | None:
    """Nothing was named — fall back to what the turn's envelope carries.
    Device geometry first: it was sent this turn, so it is fresher than an
    `area` the platform is only repeating from an earlier one."""

    if location is None:
        return None
    if location.geometry is not None:
        return ResolvedPlace(
            name=location.area or "",
            within=(),
            geometry=location.geometry,
            source=PlaceSource.ASSERTED_GEOMETRY,
        )
    if location.area:
        matches = lookup.resolve(location.area)
        if len(matches) == 1:
            return _from_match(matches[0], PlaceSource.ASSERTED_AREA)
    return None


def _first_resolved(places: list[Place]) -> ResolvedPlace | None:
    """The first place any ask in this turn actually resolved by name — what
    a placeless sibling ask borrows when the envelope gives it nothing."""

    for place in places:
        if isinstance(place, ResolvedPlace):
            return place
    return None


def _build_ask(classified: ClassifiedAsk, place: Place) -> Ask:
    return Ask(
        agriculture_subjects=classified.agriculture_subjects,
        subject_categories=classified.subject_categories,
        interaction_type=classified.interaction_type,
        place=place,
    )


def resolve_places(
    classification: IntentClassification, turn: UserTurn, *, lookup: AreaLookup
) -> Intent:
    location = turn.location
    region = location.region if location is not None else None

    # Pass 1: each ask resolves only what it names itself. A named place can
    # come back ambiguous or unresolved; those are per-ask, not fixed by a
    # sibling, so they are never overwritten below.
    places: list[Place] = [
        _resolve_named(
            classified.place_name,
            lookup,
            region,
            PlaceSource.CARRIED if classified.place_from_history else PlaceSource.NAMED,
        )
        if classified.place_name
        else None
        for classified in classification.asks
    ]

    # Pass 2: an ask that named nothing uses the turn's device/asserted
    # location — "here" means the farmer — and only without one borrows a
    # sibling's place. The classifier repeats a place that covers several
    # asks, so borrowing is the fallback, not the rule.
    sibling_place = _first_resolved(places)
    for index, classified in enumerate(classification.asks):
        if places[index] is None and not classified.place_name:
            places[index] = _resolve_from_location(location, lookup) or sibling_place

    asks = tuple(
        _build_ask(classified, place)
        for classified, place in zip(classification.asks, places, strict=True)
    )
    return Intent(asks=asks, confidence=classification.confidence)
