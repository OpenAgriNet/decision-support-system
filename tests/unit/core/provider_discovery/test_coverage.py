"""Tier 1 — where the discover call searches, and whether it can search at all.

A turn reaches discovery with a location in one of three forms: coordinates the
client sent, an area name the client sent, or a place name the intent classifier
pulled out of what the farmer said. These tests pin the order they are tried in
and what happens when none of them resolves.
"""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.provider_discovery.models import Coverage
from dss.core.provider_discovery.service import coverage_for
from dss.core.shared.models import Geometry, Location, UserTurn
from dss.ports.area_lookup import AreaMatch
from tests.support.fakes import FakeAreaLookup

RADIUS_M = 25_000

PUNE = AreaMatch(
    name="Pune",
    region="IN-MH",
    geometry=Geometry(coordinates=[74.067998, 18.571118]),
)


class _ExplodingLookup:
    """Fails the test if the lookup is consulted at all."""

    def resolve(self, name: str, region: str | None = None) -> list[AreaMatch]:
        raise AssertionError(f"the lookup should not have been called (name={name!r})")


def _turn(location: Location | None) -> UserTurn:
    return UserTurn(
        original_query="will it rain?",
        enriched_query="will it rain?",
        session_id="s-1",
        transaction_id="t-1",
        source_lang="en",
        target_lang="en",
        channel="web",
        location=location,
    )


def test_the_turns_own_geometry_wins_and_the_lookup_is_not_consulted() -> None:
    """A client that already sent coordinates is not second-guessed, and the
    cheap path stays cheap. `place_name` deliberately disagrees, so this fails
    if the precedence is the other way round.
    """

    turn = _turn(Location(geometry=Geometry(coordinates=[74.067998, 18.571118])))

    coverage = coverage_for(
        turn,
        Intent(place_name="Nagpur"),
        lookup=_ExplodingLookup(),
        radius_m=RADIUS_M,
    )

    assert coverage == Coverage(lat=18.571118, lon=74.067998, radius_m=RADIUS_M)


def test_a_place_name_from_the_intent_resolves_through_the_lookup() -> None:
    """The farmer said where they are and the client sent no coordinates —
    "I am from Pune" becomes a point to search around."""

    lookup = FakeAreaLookup({"pune": [PUNE]})

    coverage = coverage_for(
        _turn(None),
        Intent(place_name="Pune"),
        lookup=lookup,
        radius_m=RADIUS_M,
    )

    assert coverage == Coverage(lat=18.571118, lon=74.067998, radius_m=RADIUS_M)


def test_an_ambiguous_place_name_yields_no_coverage() -> None:
    """Two districts share the name and nothing says which. Guessing would send
    the discover call ~1000km wrong, so the turn goes without a spatial filter
    and the farmer gets asked instead.
    """

    bilaspur_ct = AreaMatch(
        name="Bilaspur",
        region="IN-CT",
        geometry=Geometry(coordinates=[82.115906, 22.179960]),
    )
    bilaspur_hp = AreaMatch(
        name="Bilaspur",
        region="IN-HP",
        geometry=Geometry(coordinates=[76.670218, 31.370997]),
    )
    lookup = FakeAreaLookup({"bilaspur": [bilaspur_ct, bilaspur_hp]})

    coverage = coverage_for(
        _turn(None),
        Intent(place_name="Bilaspur"),
        lookup=lookup,
        radius_m=RADIUS_M,
    )

    assert coverage is None


def test_a_name_the_index_does_not_carry_yields_no_coverage() -> None:
    """A village name resolves to nothing — the index holds districts only."""

    coverage = coverage_for(
        _turn(None),
        Intent(place_name="Shirur"),
        lookup=FakeAreaLookup({"pune": [PUNE]}),
        radius_m=RADIUS_M,
    )

    assert coverage is None


def test_an_area_the_client_sent_resolves_through_the_lookup() -> None:
    """`location.area` is the client carrying a district forward across turns —
    the farmer answered "which district?" once and the Experience API repeats it
    on every later turn, so it does not have to be re-extracted from history.
    """

    coverage = coverage_for(
        _turn(Location(area="Pune")),
        Intent(),
        lookup=FakeAreaLookup({"pune": [PUNE]}),
        radius_m=RADIUS_M,
    )

    assert coverage == Coverage(lat=18.571118, lon=74.067998, radius_m=RADIUS_M)


def test_the_clients_area_is_preferred_over_the_inferred_place_name() -> None:
    """Both present and they disagree: the client's stated area wins over a name
    an LLM pulled out of the query, because one is asserted and the other is
    inferred."""

    nagpur = AreaMatch(
        name="Nagpur",
        region="IN-MH",
        geometry=Geometry(coordinates=[79.088860, 21.146633]),
    )
    lookup = FakeAreaLookup({"pune": [PUNE], "nagpur": [nagpur]})

    coverage = coverage_for(
        _turn(Location(area="Pune")),
        Intent(place_name="Nagpur"),
        lookup=lookup,
        radius_m=RADIUS_M,
    )

    assert coverage == Coverage(lat=18.571118, lon=74.067998, radius_m=RADIUS_M)


def test_no_location_anywhere_yields_no_coverage() -> None:
    """No coordinates, no area, no place name — nowhere to search."""

    coverage = coverage_for(
        _turn(None),
        Intent(),
        lookup=_ExplodingLookup(),
        radius_m=RADIUS_M,
    )

    assert coverage is None
