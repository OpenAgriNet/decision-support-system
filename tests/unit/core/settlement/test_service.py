"""Tier 1 — settling an ask from its frame."""

from __future__ import annotations

from dss.core.forms.models import FormContext
from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    Scheme,
    SubjectCategory,
    UnclearAction,
)
from dss.core.settlement.models import (
    Ambiguous,
    AskRequirements,
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
    UnservableReason,
)
from dss.core.settlement.requirements import build_ask_requirements
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
    choices = ("Aurangabad MH", "Aurangabad BR")
    place = Slot(name=PLACE, state=Ambiguous("Aurangabad", choices))

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
    village = Slot(name=VILLAGE, state=Unservable("Rampur", UnservableReason.NO_FORM))

    assert settle(Frame(slots=(phone, village))) == CannotServe(slot=village)


def _ask(category: SubjectCategory, interaction: InteractionType) -> Ask:
    return Ask(subject_categories=category, interaction_type=interaction)


def test_each_ask_settles_on_its_own() -> None:
    """Weather needs a place and none was given; a grievance needs none."""

    weather = _ask(SubjectCategory.WEATHER, InteractionType.OBSERVE)
    grievance = _ask(SubjectCategory.GRIEVANCE, InteractionType.ACT)

    settlements = settle_asks(
        Intent(asks=(weather, grievance)),
        requirements_for=lambda ask: AskRequirements(
            needs_place=ask is weather,
            scheme_codes_with_a_form=frozenset(),
            actions_with_a_form=frozenset(),
        ),
    )

    assert settlements == (
        NeedsClarification(open_slots=(Slot(PLACE, Missing()),)),
        Settled(Frame(slots=())),
    )


def test_a_scheme_is_not_turned_away_before_the_action_is_known() -> None:
    """PM-KISAN has a status form. If the farmer wants to check a complaint,
    we can serve it, so they are asked first."""

    pm_kisan = Scheme(code="PM-KISAN", name="PM Kisan Samman Nidhi")
    problem = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        action=UnclearAction.UNCLEAR,
        scheme=pm_kisan,
    )
    forms = [
        FormContext(scheme="PMFBY", category="Grievance", action="support"),
        FormContext(scheme="PM-KISAN", category="Grievance", action="status"),
    ]

    settlements = settle_asks(
        Intent(asks=(problem,)),
        requirements_for=lambda ask: build_ask_requirements(
            ask, needs_place=False, forms=forms
        ),
    )

    assert settlements == (
        NeedsClarification(open_slots=(Slot(AskSlot.ACTION, Missing()),)),
    )
