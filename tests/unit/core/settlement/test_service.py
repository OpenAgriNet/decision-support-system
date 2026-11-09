"""Tier 1 — settling an ask from its frame."""

from __future__ import annotations

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    CannotServe,
    Frame,
    Missing,
    NeedsClarification,
    Resolved,
    Settled,
    Slot,
    Unresolved,
    UnresolvedReason,
    Unservable,
)
from dss.core.settlement.service import settle, settle_asks

PLACE = AskSlot.PLACE
# Form field names: settle works on any frame, not only an ask's.
PHONE = "phone"
VILLAGE = "village"


def test_no_slots_is_settled() -> None:
    frame = Frame(slots=())

    assert settle(frame) == Settled(frame)


def test_missing_slot_needs_clarification() -> None:
    place = Slot(name=PLACE, state=Missing())

    assert settle(Frame(slots=(place,))) == NeedsClarification(open_slots=(place,))


def test_ambiguous_slot_needs_clarification() -> None:
    place = Slot(name=PLACE, state=Ambiguous(("Aurangabad MH", "Aurangabad BR")))

    assert settle(Frame(slots=(place,))) == NeedsClarification(open_slots=(place,))


def test_unresolved_slot_needs_clarification() -> None:
    place = Slot(
        name=PLACE, state=Unresolved(value="Xyzzy", reason=UnresolvedReason.NOT_FOUND)
    )

    assert settle(Frame(slots=(place,))) == NeedsClarification(open_slots=(place,))


def test_resolved_slots_are_settled() -> None:
    frame = Frame(slots=(Slot(name=PLACE, state=Resolved("Pune")),))

    assert settle(frame) == Settled(frame)


def test_open_slots_keep_frame_order() -> None:
    phone = Slot(name=PHONE, state=Missing())
    village = Slot(name=VILLAGE, state=Resolved("Wagholi"))
    place = Slot(name=PLACE, state=Missing())

    assert settle(Frame(slots=(phone, village, place))) == NeedsClarification(
        open_slots=(phone, place)
    )


def test_unservable_slot_beats_open_ones() -> None:
    """Asking first would make the farmer answer for nothing."""

    phone = Slot(name=PHONE, state=Missing())
    village = Slot(name=VILLAGE, state=Unservable(reason="farmer declined"))

    assert settle(Frame(slots=(phone, village))) == CannotServe(slot=village)


WEATHER_PACK = "openagrinet:WeatherObservation"
GRIEVANCE_PACK = "openagrinet:Grievance"
INDEX = {
    ("Weather", "Service"): (WEATHER_PACK,),
    ("Grievance", "Service"): (GRIEVANCE_PACK,),
}


def _ask(category: SubjectCategory, interaction: InteractionType) -> Ask:
    return Ask(subject_categories=category, interaction_type=interaction)


def test_each_ask_settles_on_its_own() -> None:
    """Weather's pack needs a place and none was given; a grievance needs none."""

    weather = _ask(SubjectCategory.WEATHER, InteractionType.OBSERVE)
    grievance = _ask(SubjectCategory.GRIEVANCE, InteractionType.ACT)

    settlements = settle_asks(
        Intent(asks=(weather, grievance)),
        index=INDEX,
        place_bound_types={WEATHER_PACK},
    )

    assert settlements == (
        NeedsClarification(open_slots=(Slot(PLACE, Missing()),)),
        Settled(Frame(slots=())),
    )
