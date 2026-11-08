"""Tier 1 — an ask's own slots: whether each is needed, and its state."""

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
from dss.core.settlement.ask_slots import ask_slot_state, frame_for, needed_slots
from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    Frame,
    Missing,
    Resolved,
    Slot,
    SlotState,
    Unresolved,
    UnresolvedReason,
)
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaMatch

MANDI = "openagrinet:MandiPrice"
FACILITY = "openagrinet:AgricultureFacility"

PUNE = ResolvedPlace(
    name="Pune",
    within=("IN-MH",),
    geometry=Geometry(coordinates=[73.85, 18.52]),
    source=PlaceSource.NAMED,
)


def test_place_needed_when_every_pack_says_so() -> None:
    assert needed_slots(
        capability_types=(MANDI, FACILITY), place_bound_types={MANDI, FACILITY}
    ) == (AskSlot.PLACE,)


def test_one_placeless_pack_needs_no_place() -> None:
    assert (
        needed_slots(capability_types=(MANDI, FACILITY), place_bound_types={MANDI})
        == ()
    )


def test_no_pack_needs_no_place() -> None:
    """With no pack, nothing says a place is needed, so asking for one would
    make the farmer answer for nothing."""

    assert needed_slots(capability_types=(), place_bound_types={MANDI}) == ()


def _place_of(
    place: ResolvedPlace | AmbiguousPlace | UnresolvedPlace | None,
) -> SlotState:
    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )
    return ask_slot_state(ask, AskSlot.PLACE)


def test_resolved_place() -> None:
    assert _place_of(PUNE) == Resolved(PUNE)


def test_no_place_given() -> None:
    assert _place_of(None) == Missing()


def _aurangabad(region: str) -> AreaMatch:
    return AreaMatch(
        name="Aurangabad",
        region=region,
        within=("India",),
        geometry=Geometry(coordinates=[75.3, 19.9]),
    )


def test_ambiguous_place() -> None:
    candidates = (_aurangabad("IN-MH"), _aurangabad("IN-BR"))
    place = AmbiguousPlace(unresolved_name="Aurangabad", candidates=candidates)

    assert _place_of(place) == Ambiguous(candidates)


def test_unknown_place_is_not_found() -> None:
    place = UnresolvedPlace(unresolved_name="Xyzzy")

    assert _place_of(place) == Unresolved(
        value="Xyzzy", reason=UnresolvedReason.NOT_FOUND
    )


def test_whole_state_is_too_broad() -> None:
    """Named by the area's own spelling, so the question reads "which place
    in Maharashtra?", whatever the farmer typed."""

    place = UnresolvedPlace(unresolved_name="maharashtra", region="Maharashtra")

    assert _place_of(place) == Unresolved(
        value="Maharashtra", reason=UnresolvedReason.TOO_BROAD
    )


def _weather_at(place: ResolvedPlace | None) -> Ask:
    return Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )


def test_frame_holds_each_needed_slot() -> None:
    frame = frame_for(_weather_at(PUNE), needed=(AskSlot.PLACE,))

    assert frame == Frame(slots=(Slot(AskSlot.PLACE, Resolved(PUNE)),))


def test_frame_leaves_out_unneeded_slots() -> None:
    """A place said in passing is never asked about."""

    assert frame_for(_weather_at(PUNE), needed=()) == Frame(slots=())
