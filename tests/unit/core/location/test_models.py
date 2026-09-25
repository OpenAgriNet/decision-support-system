"""Tier 1 — the place-resolution outcome contract."""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.location.models import PlaceOutcome, PlaceResolution
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaMatch


def test_place_resolution_defaults_to_resolved_with_no_candidates() -> None:
    intent = Intent()
    resolution = PlaceResolution(intent=intent)
    assert resolution.intent == intent
    assert resolution.outcome == PlaceOutcome.RESOLVED
    assert resolution.unresolved_name is None
    assert resolution.candidates == ()


def test_place_resolution_carries_ambiguous_candidates() -> None:
    candidates = (
        AreaMatch(
            name="Bilaspur",
            region="IN-HP",
            geometry=Geometry(coordinates=[76.75, 31.33]),
        ),
        AreaMatch(
            name="Bilaspur",
            region="IN-CT",
            geometry=Geometry(coordinates=[82.15, 22.09]),
        ),
    )
    resolution = PlaceResolution(
        intent=Intent(),
        outcome=PlaceOutcome.AMBIGUOUS,
        unresolved_name="Bilaspur",
        candidates=candidates,
    )
    assert resolution.outcome == PlaceOutcome.AMBIGUOUS
    assert resolution.unresolved_name == "Bilaspur"
    assert resolution.candidates == candidates
