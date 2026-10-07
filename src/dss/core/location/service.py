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


def _name_and_part(name: str) -> tuple[str, str]:
    """ "Rampur, Himachal Pradesh" is a line we listed and the farmer picked:
    the name, then a place that contains it. No area name holds a comma."""

    lookup_name, comma, part = name.rpartition(",")
    if not comma:
        return name.strip(), ""
    return lookup_name.strip(), part.strip()


def _name_and_parts(text: str) -> tuple[str, tuple[str, ...]]:
    """A line we listed after several picks: "Nairobi, Kenya, Nakuru" is the
    name, then the places above it, coarsest first."""

    name, comma, rest = text.partition(",")
    if not comma:
        return name.strip(), ()
    return name.strip(), tuple(part.strip() for part in rest.split(",") if part.strip())


async def _all_above(name: str, parts: tuple[str, ...], lookup: AreaLookup) -> bool:
    """True when every part is a place above some match of the name."""

    matches = _drop_nested(await lookup.resolve(name))
    above = {w.casefold() for match in matches for w in match.within}
    return all(part.casefold() in above for part in parts)


async def _split_joined(
    classified: ClassifiedAsk, lookup: AreaLookup
) -> list[ClassifiedAsk]:
    """The model may join "Pune and Mumbai" into "Pune, Mumbai". If the part
    is a place of its own and not above the name, treat it as two places."""

    if not classified.place_name:
        return [classified]
    base, parts = _name_and_parts(classified.place_name)
    # "Nairobi, Kenya, Nakuru": Nakuru is a town of its own, but here it is a
    # place above the Nairobi villages. That is one place, narrowed down.
    if len(parts) > 1 and await _all_above(base, parts, lookup):
        return [classified]
    name, part = _name_and_part(classified.place_name)
    if not part or not await lookup.resolve(part):
        return [classified]
    folded = part.casefold()
    matches = _drop_nested(await lookup.resolve(name))
    if any(folded in {w.casefold() for w in m.within} for m in matches):
        return [classified]
    return [
        classified.model_copy(update={"place_name": name}),
        classified.model_copy(update={"place_name": part}),
    ]


async def _resolve_named(
    name: str, lookup: AreaLookup, region: str | None, source: PlaceSource
) -> Place:
    """A place the farmer actually said, this turn or earlier — the one case
    that can fail loud (`AmbiguousPlace`/`UnresolvedPlace`) rather than fall
    through."""

    lookup_name, parts = _name_and_parts(name)
    matches = _drop_nested(await lookup.resolve(lookup_name))
    wanted = [part.casefold() for part in parts]
    # A match that sits directly in the last part comes first. "Madhubani,
    # Bihar" is the line for the Madhubani district; a Madhubani block
    # elsewhere in Bihar is also inside Bihar, and keeping both asks the same
    # question forever.
    directly = [
        m
        for m in matches
        if wanted and m.within and m.within[-1].casefold() == wanted[-1]
    ]
    inside = [
        m
        for m in matches
        if all(part in {w.casefold() for w in m.within} for part in wanted)
    ]
    # A part that fits no match is not ignored: dropping it could answer for
    # a place the farmer did not mean.
    if wanted and not inside:
        return UnresolvedPlace(unresolved_name=name)
    matches = directly or inside or matches
    # The region is where the farmer is, not what they asked about: it only
    # breaks a tie between same-name places, never hides the one they named.
    if len(matches) > 1 and region is not None:
        matches = [m for m in matches if m.region == region] or matches
    if len(matches) == 1:
        return _from_match(matches[0], source)
    if len(matches) > 1:
        return AmbiguousPlace(unresolved_name=name, candidates=tuple(matches))
    return UnresolvedPlace(unresolved_name=name)


async def _resolve_from_location(
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
        matches = await lookup.resolve(location.area)
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


async def resolve_places(
    classification: IntentClassification, turn: UserTurn, *, lookup: AreaLookup
) -> Intent:
    location = turn.location
    region = location.region if location is not None else None
    classified_asks = [
        split
        for classified in classification.asks
        for split in await _split_joined(classified, lookup)
    ]

    # Pass 1: each ask resolves only what it names itself. A named place can
    # come back ambiguous or unresolved; those are per-ask, not fixed by a
    # sibling, so they are never overwritten below.
    places: list[Place] = [
        await _resolve_named(
            classified.place_name,
            lookup,
            region,
            PlaceSource.CARRIED if classified.place_from_history else PlaceSource.NAMED,
        )
        if classified.place_name
        else None
        for classified in classified_asks
    ]

    # Pass 2: an ask that named nothing uses the turn's device/asserted
    # location — "here" means the farmer — and only without one borrows a
    # sibling's place. The classifier repeats a place that covers several
    # asks, so borrowing is the fallback, not the rule.
    sibling_place = _first_resolved(places)
    for index, classified in enumerate(classified_asks):
        if places[index] is None and not classified.place_name:
            places[index] = (
                await _resolve_from_location(location, lookup) or sibling_place
            )

    asks = tuple(
        _build_ask(classified, place)
        for classified, place in zip(classified_asks, places, strict=True)
    )
    return Intent(asks=asks, confidence=classification.confidence)
