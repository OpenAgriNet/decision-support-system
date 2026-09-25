"""Tier 1 — where a single ask's discover call searches.

Resolution already happened in `core/location` before discovery runs, so this
reads `ask.place` directly — no lookup, no precedence to re-derive.
"""

from __future__ import annotations

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
    UnresolvedPlace,
)
from dss.core.provider_discovery.models import Coverage
from dss.core.provider_discovery.service import coverage_for_ask
from dss.core.shared.models import Geometry

RADIUS_M = 25_000


def _ask(place) -> Ask:  # noqa: ANN001
    return Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )


def test_a_resolved_place_becomes_coverage() -> None:
    place = ResolvedPlace(
        name="Pune",
        within=("IN-MH",),
        geometry=Geometry(coordinates=[74.067998, 18.571118]),
        source=PlaceSource.NAMED,
    )

    coverage = coverage_for_ask(_ask(place), radius_m=RADIUS_M)

    assert coverage == Coverage(lat=18.571118, lon=74.067998, radius_m=RADIUS_M)


def test_no_place_yields_no_coverage() -> None:
    assert coverage_for_ask(_ask(None), radius_m=RADIUS_M) is None


def test_an_ambiguous_place_yields_no_coverage() -> None:
    place = AmbiguousPlace(unresolved_name="Bilaspur", candidates=())
    assert coverage_for_ask(_ask(place), radius_m=RADIUS_M) is None


def test_an_unresolved_place_yields_no_coverage() -> None:
    place = UnresolvedPlace(unresolved_name="Shirur")
    assert coverage_for_ask(_ask(place), radius_m=RADIUS_M) is None
