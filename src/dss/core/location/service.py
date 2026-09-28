"""Resolves each ask's place from words to a `ResolvedPlace`.

The only place an `Ask` is built from a `ClassifiedAsk` — see
`core/intent/service.py`'s docstring. Precedence, one ask at a time:

    place named in this turn
      > place another ask in this turn already resolved
      > device geometry
      > client-asserted area
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
    Classification,
    ClassifiedAsk,
    Intent,
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


def _resolve_named(
    name: str, lookup: AreaLookup, region: str | None, source: PlaceSource
) -> Place:
    """A place the farmer actually said, this turn or earlier — the one case
    that can fail loud (`AmbiguousPlace`/`UnresolvedPlace`) rather than fall
    through."""

    matches = lookup.resolve(name, region)
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
    a placeless sibling ask borrows before falling back to the envelope."""

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
    classification: Classification, turn: UserTurn, *, lookup: AreaLookup
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

    # Pass 2: an ask that named nothing borrows the first place any sibling
    # in this same turn resolved, before falling back to the turn's own
    # device/asserted location.
    sibling_place = _first_resolved(places)
    for index, classified in enumerate(classification.asks):
        if places[index] is None and not classified.place_name:
            places[index] = sibling_place or _resolve_from_location(location, lookup)

    asks = tuple(
        _build_ask(classified, place)
        for classified, place in zip(classification.asks, places, strict=True)
    )
    return Intent(asks=asks, confidence=classification.confidence)
