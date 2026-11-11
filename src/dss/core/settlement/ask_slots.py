"""An ask's own slots: whether its category needs each, and what intent read
for it."""

from __future__ import annotations

from collections.abc import Collection

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    GuessedScheme,
    ResolvedPlace,
    Scheme,
    UnresolvedPlace,
)
from dss.core.provider_discovery.models import NetworkSchemaType
from dss.core.schema_packs.service import is_place_needed
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


def needed_slots(
    *,
    capability_types: Collection[NetworkSchemaType],
    place_bound_types: Collection[NetworkSchemaType],
) -> tuple[AskSlot, ...]:
    """The ask's slots its packs need, in the order they are asked."""

    if is_place_needed(capability_types, place_bound_types):
        return (AskSlot.PLACE,)
    return ()


def frame_for(ask: Ask, *, needed: tuple[AskSlot, ...]) -> Frame:
    return Frame(slots=tuple(Slot(slot, ask_slot_state(ask, slot)) for slot in needed))


def ask_slot_state(ask: Ask, slot: AskSlot) -> SlotState:
    match slot:
        case AskSlot.PLACE:
            return _place_state(ask)
        case AskSlot.SCHEME:
            return _scheme_state(ask)


def _place_state(ask: Ask) -> SlotState:
    match ask.place:
        case ResolvedPlace() as place:
            return Resolved(place)
        case AmbiguousPlace(unresolved_name=name, candidates=candidates):
            return Ambiguous(value=name, choices=candidates)
        case UnresolvedPlace(region=str(region)):
            return Unresolved(value=region, reason=UnresolvedReason.TOO_BROAD)
        case UnresolvedPlace(unresolved_name=name):
            return Unresolved(value=name, reason=UnresolvedReason.NOT_FOUND)
        case None:
            return Missing()


def _scheme_state(ask: Ask) -> SlotState:
    match ask.scheme:
        case Scheme() as scheme:
            return Resolved(scheme)
        case GuessedScheme(scheme=scheme):
            return Ambiguous(value=scheme.name, choices=(scheme,))
        case None:
            return Missing()
