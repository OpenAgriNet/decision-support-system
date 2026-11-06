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
from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    Missing,
    Resolved,
    SlotState,
    Unresolved,
    UnresolvedReason,
)


def is_place_needed(
    capability_types: Collection[str], *, place_bound_types: Collection[str]
) -> bool:
    # Unlike discovery's spatial filter, no pack means no place: nothing says
    # one is needed, and the farmer would answer for nothing.
    return bool(capability_types) and all(
        capability in place_bound_types for capability in capability_types
    )


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
