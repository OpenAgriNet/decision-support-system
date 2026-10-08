"""Tier 1 — place resolution: precedence and the two failure shapes.
`AreaLookup` is faked; no real CSV or network."""

from __future__ import annotations

from dss.core.intent.models import (
    AmbiguousPlace,
    ClassifiedAsk,
    IntentClassification,
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

    def __init__(
        self,
        table: dict[str, list[AreaMatch]],
        *,
        nearest: AreaMatch | None = None,
    ) -> None:
        self._table = table
        self._nearest = nearest
        self.calls = 0
        self.nearest_max_km: list[float] = []

    async def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        self.calls += 1
        matches = self._table.get(name.lower(), [])
        if region is None:
            return matches
        return [m for m in matches if m.region == region]

    async def nearest(self, point: Geometry, max_km: float) -> AreaMatch | None:
        self.nearest_max_km.append(max_km)
        return self._nearest


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


async def test_named_place_beats_device_geometry() -> None:
    """The headline test: today's bug. A farmer in Anand asking about Pune
    must get Pune, not the device's own location."""

    classification = IntentClassification(asks=(_weather_ask("Pune"),))
    turn = _turn(geometry=_DEVICE_GEOMETRY)
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.name == "Pune"
    assert place.geometry == _PUNE.geometry


async def test_a_place_from_history_resolves_as_carried() -> None:
    """Same lookup as a named place; only the label differs, so a reader can
    tell "you said Pune" from "you said Pune earlier"."""

    ask = ClassifiedAsk(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place_name="Pune",
        place_from_history=True,
    )
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = await resolve_places(
        IntentClassification(asks=(ask,)), _turn(), lookup=lookup
    )

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.source == PlaceSource.CARRIED


async def test_named_place_beats_asserted_area() -> None:
    """The platform's repeated `location.area` is what it asserts about the
    farmer, not what the farmer said this turn — it must not outrank that."""

    classification = IntentClassification(asks=(_weather_ask("Pune"),))
    turn = _turn(area="Anand")
    lookup = _FakeLookup({"pune": [_PUNE], "anand": [_ANAND]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.name == "Pune"


async def test_device_geometry_beats_asserted_area() -> None:
    """Live device geometry, sent this turn with location consent, is fresher
    than an area the platform is only repeating from an earlier turn."""

    classification = IntentClassification(asks=(_weather_ask(),))
    turn = _turn(area="Anand", geometry=_DEVICE_GEOMETRY)
    lookup = _FakeLookup({"anand": [_ANAND]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.geometry == _DEVICE_GEOMETRY
    assert place.source.value == "asserted_geometry"


async def test_named_place_matching_several_is_ambiguous() -> None:
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
    classification = IntentClassification(asks=(_weather_ask("Bilaspur"),))
    turn = _turn()
    lookup = _FakeLookup({"bilaspur": [bilaspur_hp, bilaspur_ct]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, AmbiguousPlace)
    assert place.unresolved_name == "Bilaspur"
    assert place.candidates == (bilaspur_hp, bilaspur_ct)


async def test_named_place_not_in_the_index_is_unresolved() -> None:
    classification = IntentClassification(asks=(_weather_ask("Xyzzy"),))
    turn = _turn()
    lookup = _FakeLookup({})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, UnresolvedPlace)
    assert place.unresolved_name == "Xyzzy"


async def test_the_farmers_region_never_hides_the_place_they_named() -> None:
    """A farmer in Gujarat asks about Pune. The region says where they are, not
    what they asked about, so it must not filter Pune out."""

    classification = IntentClassification(asks=(_weather_ask("Pune"),))
    turn = _turn(region="IN-GJ")
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = await resolve_places(classification, turn, lookup=lookup)

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


async def test_region_hint_narrows_an_otherwise_ambiguous_name() -> None:
    """`turn.location.region` disambiguates a name that would otherwise match
    several — "Bilaspur in IN-HP" is not a contradiction, it is a hint."""

    classification = IntentClassification(asks=(_weather_ask("Bilaspur"),))
    turn = _turn(region="IN-HP")
    lookup = _FakeLookup({"bilaspur": [_BILASPUR_HP, _BILASPUR_CT]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert place is not None
    assert place.name == "Bilaspur"
    assert place.geometry == _BILASPUR_HP.geometry


async def test_a_region_that_fits_no_match_still_asks_which_one() -> None:
    """A farmer in Gujarat asks about Bilaspur, which is in six states but not
    Gujarat. "I could not find Bilaspur" would be false; ask which one."""

    classification = IntentClassification(asks=(_weather_ask("Bilaspur"),))
    turn = _turn(region="IN-GJ")
    lookup = _FakeLookup({"bilaspur": [_BILASPUR_HP, _BILASPUR_CT]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, AmbiguousPlace)
    assert place.candidates == (_BILASPUR_HP, _BILASPUR_CT)


_RAMPUR_UP = AreaMatch(
    name="Rampur",
    region="IN-UP",
    within=("India", "Uttar Pradesh"),
    geometry=Geometry(coordinates=[79.03, 28.81]),
)
_RAMPUR_HP = AreaMatch(
    name="Rampur",
    region="IN-HP",
    within=("India", "Himachal Pradesh"),
    geometry=Geometry(coordinates=[77.63, 31.45]),
)


async def test_a_name_with_a_part_picks_the_match_inside_that_part() -> None:
    """The farmer answered "which Rampur?" — the model copies the line we
    listed, "Rampur, Himachal Pradesh". The part after the comma picks one."""

    classification = IntentClassification(
        asks=(_weather_ask("Rampur, Himachal Pradesh"),)
    )
    lookup = _FakeLookup({"rampur": [_RAMPUR_UP, _RAMPUR_HP]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.name == "Rampur"
    assert place.geometry == _RAMPUR_HP.geometry


async def test_the_region_narrows_a_long_list_before_anything_is_listed() -> None:
    """Six Rampurs are too many to list. A farmer in Himachal is asked
    nothing: the region already leaves one."""

    others = [
        AreaMatch(
            name="Rampur",
            region=f"IN-{code}",
            within=("India", state),
            geometry=Geometry(coordinates=[80.0 + i, 25.0]),
        )
        for i, (code, state) in enumerate(
            [
                ("OD", "Odisha"),
                ("BR", "Bihar"),
                ("JH", "Jharkhand"),
                ("MP", "Madhya Pradesh"),
            ]
        )
    ]
    classification = IntentClassification(asks=(_weather_ask("Rampur"),))
    lookup = _FakeLookup({"rampur": [_RAMPUR_UP, *others, _RAMPUR_HP]})

    intent = await resolve_places(classification, _turn(region="IN-HP"), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.geometry == _RAMPUR_HP.geometry


async def test_a_part_that_leaves_several_asks_again_with_only_those() -> None:
    """Two Rampurs in Uttar Pradesh: "Rampur, Uttar Pradesh" drops the
    Himachal one but cannot choose between the rest. The next question lists
    just those two, one level down."""

    rampur_up_b = AreaMatch(
        name="Rampur",
        region="IN-UP",
        within=("India", "Uttar Pradesh", "Moradabad"),
        geometry=Geometry(coordinates=[78.9, 28.8]),
    )
    rampur_up_a = AreaMatch(
        name="Rampur",
        region="IN-UP",
        within=("India", "Uttar Pradesh", "Rampur"),
        geometry=Geometry(coordinates=[79.03, 28.81]),
    )
    classification = IntentClassification(asks=(_weather_ask("Rampur, Uttar Pradesh"),))
    lookup = _FakeLookup({"rampur": [rampur_up_a, _RAMPUR_HP, rampur_up_b]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, AmbiguousPlace)
    assert place.candidates == (rampur_up_a, rampur_up_b)


async def test_a_part_picks_the_match_that_sits_directly_in_it() -> None:
    """Madhubani is a district in Bihar and a block in another Bihar district.
    The question lists "Madhubani, Bihar" for the district. Every match is in
    Bihar, so reading the part as "anywhere inside" asks the same question
    forever. The district sits directly in Bihar, so it is the one meant."""

    district = AreaMatch(
        name="Madhubani",
        region="IN-BR",
        within=("India", "Bihar"),
        geometry=Geometry(coordinates=[86.08, 26.35]),
    )
    block = AreaMatch(
        name="Madhubani",
        region="IN-BR",
        within=("India", "Bihar", "Pashchim Champaran"),
        geometry=Geometry(coordinates=[84.5, 27.0]),
    )
    classification = IntentClassification(asks=(_weather_ask("Madhubani, Bihar"),))
    lookup = _FakeLookup({"madhubani": [district, block]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.within == ("India", "Bihar")


async def test_reply_not_in_list_is_not_found() -> None:
    """A reply naming a state that is not on our list is not found. The list
    is still in the history to pick from."""

    classification = IntentClassification(asks=(_weather_ask("Rampur, Kerala"),))
    lookup = _FakeLookup({"rampur": [_RAMPUR_UP, _RAMPUR_HP]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    assert intent.asks[0].place == UnresolvedPlace(unresolved_name="Rampur, Kerala")


async def test_part_we_do_not_have_is_not_found() -> None:
    """Only the Bihar Aurangabad is in the list. Ignoring "Maharashtra" would
    answer for a place about 1,000 km away."""

    bihar = AreaMatch(
        name="Aurangabad",
        region="IN-BR",
        within=("India", "Bihar"),
        geometry=Geometry(coordinates=[84.37, 24.75]),
    )
    classification = IntentClassification(
        asks=(_weather_ask("Aurangabad, Maharashtra"),)
    )
    lookup = _FakeLookup({"aurangabad": [bihar]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    assert intent.asks[0].place == UnresolvedPlace(
        unresolved_name="Aurangabad, Maharashtra"
    )


async def test_split_two_joined_places() -> None:
    """The model joined "Pune and Mumbai" into "Pune, Mumbai". Mumbai is a
    place, not above Pune, so both are answered."""

    mumbai = AreaMatch(
        name="Mumbai",
        region="IN-MH",
        within=("India", "Maharashtra"),
        geometry=Geometry(coordinates=[72.88, 19.08]),
    )
    classification = IntentClassification(asks=(_weather_ask("Pune, Mumbai"),))
    lookup = _FakeLookup({"pune": [_PUNE], "mumbai": [mumbai]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    places = [ask.place for ask in intent.asks]
    assert all(isinstance(place, ResolvedPlace) for place in places)
    assert [place.name for place in places] == ["Pune", "Mumbai"]
    assert all(ask.subject_categories is SubjectCategory.WEATHER for ask in intent.asks)


_NAIROBI_CITY = AreaMatch(
    name="Nairobi",
    region="KE",
    within=("Kenya", "Nairobi County"),
    geometry=Geometry(coordinates=[36.82, -1.29]),
)
_NAIROBI_RONGAI = AreaMatch(
    name="Nairobi",
    region="KE",
    within=("Kenya", "Nakuru", "Rongai"),
    geometry=Geometry(coordinates=[36.12, -0.04]),
)
_NAIROBI_BAHATI = AreaMatch(
    name="Nairobi",
    region="KE",
    within=("Kenya", "Nakuru", "Bahati"),
    geometry=Geometry(coordinates=[36.10, -0.16]),
)
_NAIROBI_TANGA = AreaMatch(
    name="Nairobi",
    region="TZ",
    within=("Tanzania", "Tanga Region", "Mkinga"),
    geometry=Geometry(coordinates=[39.0, -4.7]),
)
# A place of its own, and also a part of the villages' chains above.
_NAKURU_TOWN = AreaMatch(
    name="Nakuru",
    region="KE",
    within=("Kenya",),
    geometry=Geometry(coordinates=[36.07, -0.28]),
)
_NAIROBIS = {
    "nairobi": [_NAIROBI_CITY, _NAIROBI_RONGAI, _NAIROBI_BAHATI, _NAIROBI_TANGA],
    "nakuru": [_NAKURU_TOWN],
}


async def test_a_pick_with_two_parts_stays_one_place() -> None:
    """After "Kenya" the question lists "Nairobi, Kenya, Nakuru". Nakuru is a
    town of its own, but here it is a part above the villages. Reading the
    line as two places would answer for the town the farmer did not ask about."""

    classification = IntentClassification(
        asks=(_weather_ask("Nairobi, Kenya, Nakuru"),)
    )

    intent = await resolve_places(
        classification, _turn(), lookup=_FakeLookup(_NAIROBIS)
    )

    assert len(intent.asks) == 1


async def test_a_pick_with_two_parts_keeps_only_the_matches_inside() -> None:
    """Both parts narrow the list: the Kenyan Nairobis inside Nakuru. The city
    and the Tanzanian hamlet drop out, and two villages are left to choose from."""

    classification = IntentClassification(
        asks=(_weather_ask("Nairobi, Kenya, Nakuru"),)
    )

    intent = await resolve_places(
        classification, _turn(), lookup=_FakeLookup(_NAIROBIS)
    )

    place = intent.asks[0].place
    assert isinstance(place, AmbiguousPlace)
    assert {match.within for match in place.candidates} == {
        _NAIROBI_RONGAI.within,
        _NAIROBI_BAHATI.within,
    }


async def test_a_pick_that_no_longer_fits_is_not_found() -> None:
    """The source's answers changed since we asked: no Nairobi sits in Nakuru
    now. "Kenya" still shows the farmer was narrowing one place, so this is
    one place not found, never an answer for Nakuru town."""

    classification = IntentClassification(
        asks=(_weather_ask("Nairobi, Kenya, Nakuru"),)
    )
    lookup = _FakeLookup({"nairobi": [_NAIROBI_CITY], "nakuru": [_NAKURU_TOWN]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    assert [ask.place for ask in intent.asks] == [
        UnresolvedPlace(unresolved_name="Nairobi, Kenya, Nakuru")
    ]


async def test_a_pick_with_three_parts_resolves_the_one_match() -> None:
    """One level further down, the last part names the village."""

    classification = IntentClassification(
        asks=(_weather_ask("Nairobi, Kenya, Nakuru, Rongai"),)
    )

    intent = await resolve_places(
        classification, _turn(), lookup=_FakeLookup(_NAIROBIS)
    )

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.geometry == _NAIROBI_RONGAI.geometry


_MAHARASHTRA = AreaMatch(
    name="Maharashtra",
    region="IN-MH",
    within=("India",),
    geometry=Geometry(coordinates=[75.7, 19.4]),
    is_region=True,
)


async def test_a_whole_state_is_not_used_as_the_place() -> None:
    """Maharashtra is too big to search around one point. The farmer is
    asked for a smaller place, never answered for the state's middle."""

    classification = IntentClassification(asks=(_weather_ask("Maharashtra"),))
    lookup = _FakeLookup({"maharashtra": [_MAHARASHTRA]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    assert intent.asks[0].place == UnresolvedPlace(
        unresolved_name="Maharashtra", region="Maharashtra"
    )


_RAJASTHAN = AreaMatch(
    name="Rajasthan",
    region="IN-RJ",
    within=("India",),
    geometry=Geometry(coordinates=[74.2, 26.6]),
    is_region=True,
)
_JAISALMER = AreaMatch(
    name="Jaisalmer",
    region="IN-RJ",
    within=("India", "Rajasthan"),
    geometry=Geometry(coordinates=[70.91, 26.92]),
)


async def test_a_named_state_narrows_to_the_farmers_own_area() -> None:
    """A farmer in Jaisalmer asks about "Rajasthan". They almost surely mean
    where they are, and the platform says that is Jaisalmer."""

    classification = IntentClassification(asks=(_weather_ask("Rajasthan"),))
    lookup = _FakeLookup({"rajasthan": [_RAJASTHAN], "jaisalmer": [_JAISALMER]})

    intent = await resolve_places(
        classification, _turn(area="Jaisalmer"), lookup=lookup
    )

    assert intent.asks[0].place == ResolvedPlace(
        name="Jaisalmer",
        within=("India", "Rajasthan"),
        geometry=_JAISALMER.geometry,
        source=PlaceSource.NEAR_USER,
    )


async def test_a_named_state_narrows_to_the_users_device_point() -> None:
    """Only a device point is sent. The nearest known place, Jaisalmer, is in
    Rajasthan, so the point is used and named after it. The name lets the
    answer say where it is about."""

    point = Geometry(coordinates=[70.95, 26.90])
    classification = IntentClassification(asks=(_weather_ask("Rajasthan"),))
    lookup = _FakeLookup({"rajasthan": [_RAJASTHAN]}, nearest=_JAISALMER)

    intent = await resolve_places(classification, _turn(geometry=point), lookup=lookup)

    assert intent.asks[0].place == ResolvedPlace(
        name="Jaisalmer",
        within=("India", "Rajasthan"),
        geometry=point,
        source=PlaceSource.NEAR_USER,
    )


async def test_a_device_point_in_another_state_still_asks() -> None:
    """The nearest known place is in Gujarat, so the user may not be in the
    Rajasthan they named. Answering for their point would be a guess."""

    gujarat_place = AreaMatch(
        name="Banaskantha",
        region="IN-GJ",
        within=("India", "Gujarat"),
        geometry=Geometry(coordinates=[72.4, 24.2]),
    )
    classification = IntentClassification(asks=(_weather_ask("Rajasthan"),))
    lookup = _FakeLookup({"rajasthan": [_RAJASTHAN]}, nearest=gujarat_place)

    intent = await resolve_places(
        classification,
        _turn(geometry=Geometry(coordinates=[72.5, 24.4])),
        lookup=lookup,
    )

    assert intent.asks[0].place == UnresolvedPlace(
        unresolved_name="Rajasthan", region="Rajasthan"
    )


async def test_the_nearest_place_guard_is_configurable() -> None:
    """How far a device point may be from a known place is a deployment's
    choice: a country with few known places needs a wider guard."""

    classification = IntentClassification(asks=(_weather_ask("Rajasthan"),))
    lookup = _FakeLookup({"rajasthan": [_RAJASTHAN]}, nearest=_JAISALMER)

    await resolve_places(
        classification,
        _turn(geometry=Geometry(coordinates=[70.95, 26.90])),
        lookup=lookup,
        nearest_max_km=12.5,
    )

    assert lookup.nearest_max_km == [12.5]


async def test_an_asserted_state_is_not_used_as_the_place() -> None:
    """The platform may send only the farmer's state as their area. That is
    no better a search point than a named state, so the ask has no place."""

    classification = IntentClassification(asks=(_weather_ask(None),))
    lookup = _FakeLookup({"maharashtra": [_MAHARASHTRA]})

    intent = await resolve_places(
        classification, _turn(area="Maharashtra"), lookup=lookup
    )

    assert intent.asks[0].place is None


async def test_a_state_after_a_comma_is_never_a_second_place() -> None:
    """There is no Aurangabad in Maharashtra in the index, only one in Bihar.
    Maharashtra is a region, not a place of its own, so the reply is not two
    places. Splitting it would answer for Bihar's Aurangabad, far away."""

    aurangabad_bihar = AreaMatch(
        name="Aurangabad",
        region="IN-BR",
        within=("India", "Bihar"),
        geometry=Geometry(coordinates=[84.37, 24.75]),
    )
    classification = IntentClassification(
        asks=(_weather_ask("Aurangabad, Maharashtra"),)
    )
    lookup = _FakeLookup(
        {"aurangabad": [aurangabad_bihar], "maharashtra": [_MAHARASHTRA]}
    )

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    assert [ask.place for ask in intent.asks] == [
        UnresolvedPlace(unresolved_name="Aurangabad, Maharashtra")
    ]


async def test_a_territory_and_its_district_of_one_name_stay_one_place() -> None:
    """Chandigarh is a territory and the only district in it. The region
    must not swallow the district and make "Chandigarh, Chandigarh" read as
    two places."""

    district = AreaMatch(
        name="Chandigarh",
        region="IN-CH",
        within=("India", "Chandigarh"),
        geometry=Geometry(coordinates=[76.78, 30.73]),
    )
    territory = district.model_copy(update={"within": ("India",), "is_region": True})
    classification = IntentClassification(
        asks=(_weather_ask("Chandigarh, Chandigarh"),)
    )
    lookup = _FakeLookup({"chandigarh": [district, territory]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    assert [ask.place.geometry for ask in intent.asks] == [district.geometry]


async def test_a_block_inside_its_same_name_district_resolves_to_the_district() -> None:
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
    classification = IntentClassification(asks=(_weather_ask("Nashik"),))
    lookup = _FakeLookup({"nashik": [block, district]})

    intent = await resolve_places(classification, _turn(), lookup=lookup)

    place = intent.asks[0].place
    assert isinstance(place, ResolvedPlace)
    assert place.within == district.within


async def test_two_asks_two_places() -> None:
    """ "Wheat price in Pune and will it rain in Anand?" — two asks, two
    independently resolved places."""

    market_ask = ClassifiedAsk(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
        agriculture_subjects="wheat",
        place_name="Pune",
    )
    classification = IntentClassification(asks=(market_ask, _weather_ask("Anand")))
    turn = _turn()
    lookup = _FakeLookup({"pune": [_PUNE], "anand": [_ANAND]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    places = [ask.place for ask in intent.asks]
    assert places[0] is not None and places[0].name == "Pune"
    assert places[1] is not None and places[1].name == "Anand"


async def test_an_ask_naming_no_place_uses_the_device_before_a_sibling() -> None:
    """ "Onion price in Pune, and will it rain here?" from Anand. "Here" is
    the device, not Pune."""

    rain_here = _weather_ask()
    classification = IntentClassification(asks=(_weather_ask("Pune"), rain_here))
    turn = _turn(geometry=_DEVICE_GEOMETRY)
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    place = intent.asks[1].place
    assert place is not None
    assert place.geometry == _DEVICE_GEOMETRY


async def test_two_asks_one_place() -> None:
    """ "Wheat price and will it rain in Pune?" with no device location — one
    ask names the place, the other names none, and with nothing else to go
    on it reuses what its sibling resolved rather than having no place."""

    market_ask = ClassifiedAsk(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
        agriculture_subjects="wheat",
        place_name=None,
    )
    classification = IntentClassification(asks=(market_ask, _weather_ask("Pune")))
    turn = _turn()
    lookup = _FakeLookup({"pune": [_PUNE]})

    intent = await resolve_places(classification, turn, lookup=lookup)

    assert intent.asks[0].place is not None
    assert intent.asks[0].place.name == "Pune"
    assert intent.asks[1].place is not None
    assert intent.asks[1].place.name == "Pune"


async def test_empty_classification_never_touches_the_lookup() -> None:
    lookup = _FakeLookup({})

    intent = await resolve_places(IntentClassification(), _turn(), lookup=lookup)

    assert intent.asks == ()
    assert lookup.calls == 0


async def test_nothing_named_and_no_location_leaves_place_none() -> None:
    classification = IntentClassification(asks=(_weather_ask(),))
    turn = _turn()
    lookup = _FakeLookup({})

    intent = await resolve_places(classification, turn, lookup=lookup)

    assert intent.asks[0].place is None
