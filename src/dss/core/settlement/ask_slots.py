"""An ask's own slots: whether its category needs each, and what intent read
for it."""

from __future__ import annotations

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    GuessedScheme,
    ResolvedPlace,
    Scheme,
    UnresolvedPlace,
)
from dss.core.settlement.models import (
    Ambiguous,
    AskNeeds,
    AskSlot,
    Frame,
    Missing,
    Resolved,
    Slot,
    SlotState,
    Unresolved,
    UnresolvedReason,
    Unservable,
)


def needed_slots(needs: AskNeeds) -> tuple[AskSlot, ...]:
    """In the order they are asked."""

    is_needed = {
        AskSlot.SCHEME: bool(needs.schemes_with_a_form),
        AskSlot.PLACE: needs.needs_place,
    }
    return tuple(slot for slot, needed in is_needed.items() if needed)


def frame_for(ask: Ask, needs: AskNeeds) -> Frame:
    return Frame(
        slots=tuple(
            Slot(slot, ask_slot_state(ask, slot, needs)) for slot in needed_slots(needs)
        )
    )


def ask_slot_state(ask: Ask, slot: AskSlot, needs: AskNeeds) -> SlotState:
    match slot:
        case AskSlot.PLACE:
            return _place_state(ask)
        case AskSlot.SCHEME:
            return _scheme_state(ask, needs.schemes_with_a_form)


def _place_state(ask: Ask) -> SlotState:
    match ask.place:
        case ResolvedPlace() as place:
            return Resolved(place)
        case AmbiguousPlace(candidates=(guess,)):
            return Ambiguous(value=guess.name, choices=(guess,))
        case AmbiguousPlace(unresolved_name=name, candidates=candidates):
            return Ambiguous(value=name, choices=candidates)
        case UnresolvedPlace(region=str(region)):
            return Unresolved(value=region, reason=UnresolvedReason.TOO_BROAD)
        case UnresolvedPlace(unresolved_name=name):
            return Unresolved(value=name, reason=UnresolvedReason.NOT_FOUND)
        case None:
            return Missing()


def _scheme_state(ask: Ask, schemes_with_a_form: frozenset[str]) -> SlotState:
    match ask.scheme:
        case Scheme(code=code) as scheme if code in schemes_with_a_form:
            return Resolved(scheme)
        case Scheme(name=name):
            # Not asked again: the farmer has no other scheme to name.
            category = ask.subject_categories.value.lower()
            return Unservable(reason=f"{name} has no {category} form.")
        case GuessedScheme(scheme=scheme) if scheme.code in schemes_with_a_form:
            return Ambiguous(value=scheme.name, choices=(scheme,))
        # A guess without a form is not confirmed: a yes could not be served.
        case GuessedScheme() | None:
            return Missing()
