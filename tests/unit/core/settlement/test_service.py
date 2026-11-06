"""Tier 1 — settling an ask from its frame."""

from __future__ import annotations

from dss.core.settlement.models import (
    CannotServe,
    Filled,
    Frame,
    Missing,
    NeedsAnswer,
    Settled,
    Slot,
    SlotName,
    Unservable,
)
from dss.core.settlement.service import settle

ACTION = SlotName("action")
SCHEME = SlotName("scheme")
PLACE = SlotName("place")


def test_no_slots_is_settled() -> None:
    frame = Frame(slots=())

    assert settle(frame) == Settled(frame)


def test_missing_slot_needs_answer() -> None:
    place = Slot(name=PLACE, state=Missing())

    assert settle(Frame(slots=(place,))) == NeedsAnswer(open_slots=(place,))


def test_filled_slots_are_settled() -> None:
    frame = Frame(slots=(Slot(name=PLACE, state=Filled("Pune")),))

    assert settle(frame) == Settled(frame)


def test_open_slots_keep_frame_order() -> None:
    action = Slot(name=ACTION, state=Missing())
    scheme = Slot(name=SCHEME, state=Filled("PMFBY"))
    place = Slot(name=PLACE, state=Missing())

    assert settle(Frame(slots=(action, scheme, place))) == NeedsAnswer(
        open_slots=(action, place)
    )


def test_unservable_slot_beats_open_ones() -> None:
    """Asking first would make the farmer answer for nothing."""

    action = Slot(name=ACTION, state=Missing())
    scheme = Slot(name=SCHEME, state=Unservable(reason="no form for PMFBY"))

    assert settle(Frame(slots=(action, scheme))) == CannotServe(slot=scheme)
