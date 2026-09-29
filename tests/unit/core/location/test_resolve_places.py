"""Tier 1 — place resolution: precedence and the two failure shapes.
`AreaLookup` is faked; no real CSV or network."""

from __future__ import annotations

from dss.core.intent.models import (
    AmbiguousPlace,
    Classification,
    ClassifiedAsk,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
    UnresolvedPlace,
)
from dss.core.location.service import resolve_places
from dss.core.shared.models import Geometry, Location, UserTurn
from dss.ports.area_lookup import AreaMatch


class _FakeLookup:
    """Resolves from a fixed table keyed by lowercase name."""

    def __init__(self, table: dict[str, list[AreaMatch]]) -> None:
        self._table = table
        self.calls = 0

    def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        self.calls += 1
        matches = self._table.get(name.lower(), [])
        if region is None:
            return matches
        return [m for m in matches if m.region == region]


_PUNE = AreaMatch(
    name="Pune",
    region="IN-MH",
    within=("India", "Maharashtra"),
    geometry=Geometry(coordinates=[73.85, 18.52]),
)
_ANAND = AreaMatch(
    name="Anand",
    region="IN-GJ",
    within=("India", "Gujarat"),
    geometry=Geometry(coordinates=[72.95, 22.56]),
)
_DEVICE_GEOMETRY = Geometry(coordinates=[72.93, 22.55])  # Anand, device-reported


def _turn(
    *,
    area: str | None = None,
    geometry: Geometry | None = None,
    region: str | None = None,
) -> UserTurn:
    location = None
    if area is not None or geometry is not None or region is not None:
        location = Location(area=area, geometry=geometry, region=region)
    return UserTurn(
        original_query="q",
        enriched_query="q",
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        location=location,
    )


def _weather_ask(place_name: str | None = None) -> ClassifiedAsk:
    return ClassifiedAsk(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place_name=place_name,
    )


def test_named_place_beats_device_geometry() -> None:
    """The headline test: today's bug. A farmer in Anand asking about Pune
    must get Pune, not the device's own location."""

    classification = Classification(asks=(_weather_ask("Pune"),))
    turn = _turn(geometry=_DEVICE_GEOMETRY)
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.name == "Pune"
    assert place.geometry == _PUNE.geometry


def test_a_place_from_history_resolves_as_carried() -> None:
    """Same lookup as a named place; only the label differs, so a reader can
    tell "you said Pune" from "you said Pune earlier"."""

    ask = ClassifiedAsk(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place_name="Pune",
        place_from_history=True,
    )
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = resolve_places(Classification(asks=(ask,)), _turn(), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.source == PlaceSource.CARRIED


def test_named_place_beats_asserted_area() -> None:
    """The platform's repeated `location.area` is what it asserts about the
    farmer, not what the farmer said this turn — it must not outrank that."""

    classification = Classification(asks=(_weather_ask("Pune"),))
    turn = _turn(area="Anand")
    lookup = _FakeLookup({"pune": [_PUNE], "anand": [_ANAND]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.name == "Pune"


def test_device_geometry_beats_asserted_area() -> None:
    """Live device geometry, sent this turn with location consent, is fresher
    than an area the platform is only repeating from an earlier turn."""

    classification = Classification(asks=(_weather_ask(),))
    turn = _turn(area="Anand", geometry=_DEVICE_GEOMETRY)
    lookup = _FakeLookup({"anand": [_ANAND]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.geometry == _DEVICE_GEOMETRY
    assert place.source.value == "asserted_geometry"


def test_named_place_matching_several_is_ambiguous() -> None:
    """Two districts named Bilaspur collide — picking one silently would be a
    ~1000km error, so the ask carries every candidate instead."""

    bilaspur_hp = AreaMatch(
        name="Bilaspur",
        region="IN-HP",
        within=("India", "Himachal Pradesh"),
        geometry=Geometry(coordinates=[76.75, 31.33]),
    )
    bilaspur_ct = AreaMatch(
        name="Bilaspur",
        region="IN-CT",
        within=("India", "Chhattisgarh"),
        geometry=Geometry(coordinates=[82.15, 22.09]),
    )
    classification = Classification(asks=(_weather_ask("Bilaspur"),))
    turn = _turn()
    lookup = _FakeLookup({"bilaspur": [bilaspur_hp, bilaspur_ct]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, AmbiguousPlace)
    assert place.unresolved_name == "Bilaspur"
    assert place.candidates == (bilaspur_hp, bilaspur_ct)


def test_named_place_not_in_the_index_is_unresolved() -> None:
    classification = Classification(asks=(_weather_ask("Xyzzy"),))
    turn = _turn()
    lookup = _FakeLookup({})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, UnresolvedPlace)
    assert place.unresolved_name == "Xyzzy"


def test_the_farmers_region_never_hides_the_place_they_named() -> None:
    """A farmer in Gujarat asks about Pune. The region says where they are, not
    what they asked about, so it must not filter Pune out."""

    classification = Classification(asks=(_weather_ask("Pune"),))
    turn = _turn(region="IN-GJ")
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.name == "Pune"


_BILASPUR_HP = AreaMatch(
    name="Bilaspur",
    region="IN-HP",
    within=("India", "Himachal Pradesh"),
    geometry=Geometry(coordinates=[76.75, 31.33]),
)
_BILASPUR_CT = AreaMatch(
    name="Bilaspur",
    region="IN-CT",
    within=("India", "Chhattisgarh"),
    geometry=Geometry(coordinates=[82.15, 22.09]),
)


def test_region_hint_narrows_an_otherwise_ambiguous_name() -> None:
    """`turn.location.region` disambiguates a name that would otherwise match
    several — "Bilaspur in IN-HP" is not a contradiction, it is a hint."""

    classification = Classification(asks=(_weather_ask("Bilaspur"),))
    turn = _turn(region="IN-HP")
    lookup = _FakeLookup({"bilaspur": [_BILASPUR_HP, _BILASPUR_CT]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.name == "Bilaspur"
    assert place.geometry == _BILASPUR_HP.geometry


def test_a_region_that_fits_no_match_still_asks_which_one() -> None:
    """A farmer in Gujarat asks about Bilaspur, which is in six states but not
    Gujarat. "I could not find Bilaspur" would be false; ask which one."""

    classification = Classification(asks=(_weather_ask("Bilaspur"),))
    turn = _turn(region="IN-GJ")
    lookup = _FakeLookup({"bilaspur": [_BILASPUR_HP, _BILASPUR_CT]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, AmbiguousPlace)
    assert place.candidates == (_BILASPUR_HP, _BILASPUR_CT)


def test_a_block_inside_its_same_name_district_resolves_to_the_district() -> None:
    """Nashik district holds a Nashik block. The district covers the block, so
    asking "which Nashik?" would make the farmer pick between near-equal
    answers."""

    district = AreaMatch(
        name="Nashik",
        region="IN-MH",
        within=("India", "Maharashtra"),
        geometry=Geometry(coordinates=[73.79, 20.0]),
    )
    block = AreaMatch(
        name="Nashik",
        region="IN-MH",
        within=("India", "Maharashtra", "Nashik"),
        geometry=Geometry(coordinates=[73.79, 20.0]),
    )
    classification = Classification(asks=(_weather_ask("Nashik"),))
    lookup = _FakeLookup({"nashik": [block, district]})

    intent = resolve_places(classification, _turn(), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.within == district.within


def test_two_asks_two_places() -> None:
    """ "Wheat price in Pune and will it rain in Anand?" — two asks, two
    independently resolved places."""

    market_ask = ClassifiedAsk(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
        agriculture_subjects="wheat",
        place_name="Pune",
    )
    classification = Classification(asks=(market_ask, _weather_ask("Anand")))
    turn = _turn()
    lookup = _FakeLookup({"pune": [_PUNE], "anand": [_ANAND]})

    intent = resolve_places(classification, turn, lookup=lookup)

    places = [ask.place for ask in intent.asks]
    assert places[0] is not None and places[0].name == "Pune"
    assert places[1] is not None and places[1].name == "Anand"


def test_an_ask_naming_no_place_uses_the_device_before_a_sibling() -> None:
    """ "Onion price in Pune, and will it rain here?" from Anand. "Here" is
    the device, not Pune."""

    rain_here = _weather_ask()
    classification = Classification(asks=(_weather_ask("Pune"), rain_here))
    turn = _turn(geometry=_DEVICE_GEOMETRY)
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[1].place
    assert place is not None
    assert place.geometry == _DEVICE_GEOMETRY


def test_two_asks_one_place() -> None:
    """ "Wheat price and will it rain in Pune?" with no device location — one
    ask names the place, the other names none, and with nothing else to go
    on it reuses what its sibling resolved rather than having no place."""

    market_ask = ClassifiedAsk(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
        agriculture_subjects="wheat",
        place_name=None,
    )
    classification = Classification(asks=(market_ask, _weather_ask("Pune")))
    turn = _turn()
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = resolve_places(classification, turn, lookup=lookup)

    assert intent.asks[0].place is not None
    assert intent.asks[0].place.name == "Pune"
    assert intent.asks[1].place is not None
    assert intent.asks[1].place.name == "Pune"


def test_empty_classification_never_touches_the_lookup() -> None:
    lookup = _FakeLookup({})

    intent = resolve_places(Classification(), _turn(), lookup=lookup)

    assert intent.asks == ()
    assert lookup.calls == 0


def test_nothing_named_and_no_location_leaves_place_none() -> None:
    classification = Classification(asks=(_weather_ask(),))
    turn = _turn()
    lookup = _FakeLookup({})

    intent = resolve_places(classification, turn, lookup=lookup)

    assert intent.asks[0].place is None
