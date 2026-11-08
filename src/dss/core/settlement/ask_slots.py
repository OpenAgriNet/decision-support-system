"""An ask's own slots: whether its category needs each, and what intent read
for it."""

from __future__ import annotations

from collections.abc import Collection

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    ResolvedPlace,
    UnresolvedPlace,
)
from dss.core.provider_discovery.models import NetworkSchemaType
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

    if _is_place_needed(capability_types, place_bound_types):
        return (AskSlot.PLACE,)
    return ()


def _is_place_needed(
    capability_types: Collection[NetworkSchemaType],
    place_bound_types: Collection[NetworkSchemaType],
) -> bool:
    return bool(capability_types) and all(
        capability in place_bound_types for capability in capability_types
    )


def frame_for(ask: Ask, *, needed: tuple[AskSlot, ...]) -> Frame:
    return Frame(slots=tuple(Slot(slot, ask_slot_state(ask, slot)) for slot in needed))


def ask_slot_state(ask: Ask, slot: AskSlot) -> SlotState:
    match slot:
        case AskSlot.PLACE:
            return _place_state(ask)


def _place_state(ask: Ask) -> SlotState:
    match ask.place:
        case ResolvedPlace() as place:
            return Resolved(place)
        case AmbiguousPlace(candidates=candidates):
            return Ambiguous(candidates)
        case UnresolvedPlace(region=str(region)):
            return Unresolved(value=region, reason=UnresolvedReason.TOO_BROAD)
        case UnresolvedPlace(unresolved_name=name):
            return Unresolved(value=name, reason=UnresolvedReason.NOT_FOUND)
        case None:
            return Missing()
