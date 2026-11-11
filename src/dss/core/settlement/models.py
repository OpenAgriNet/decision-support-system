from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# An `AskSlot`, or a field name from a form pack.
type SlotName = str


class AskSlot(StrEnum):
    """The facts intent reads from the message for every ask. Fixed by the
    product, unlike form fields, which come from the packs."""

    PLACE = "place"


@dataclass(frozen=True)
class Resolved[T]:
    value: T
    is_settled = True
    ends_ask = False


@dataclass(frozen=True)
class Missing:
    is_settled = False
    ends_ask = False


@dataclass(frozen=True)
class Ambiguous[T]:
    """A value given for this slot that fits more than one choice. One choice
    is a confirm: "Do you mean X?"."""

    value: str
    choices: tuple[T, ...]
    is_settled = False
    ends_ask = False


class UnresolvedReason(StrEnum):
    NOT_FOUND = "not_found"
    TOO_BROAD = "too_broad"


@dataclass(frozen=True)
class Unresolved:
    """A value given for this slot that we cannot use."""

    value: str
    reason: UnresolvedReason
    is_settled = False
    ends_ask = False


@dataclass(frozen=True)
class Unservable:
    reason: str
    is_settled = False
    ends_ask = True


# Closed on purpose: every slot uses these, whatever value it holds, so a new
# slot needs no new state. A new state is a design change.
type SlotState = Resolved[Any] | Ambiguous[Any] | Unresolved | Missing | Unservable


@dataclass(frozen=True)
class Slot:
    name: SlotName
    state: SlotState


@dataclass(frozen=True)
class Frame:
    """An ask's slots, in the order they are asked."""

    slots: tuple[Slot, ...]

    def __post_init__(self) -> None:
        names = [slot.name for slot in self.slots]
        if repeated := sorted({name for name in names if names.count(name) > 1}):
            raise ValueError(f"slots named more than once: {', '.join(repeated)}")

    @property
    def ending_slot(self) -> Slot | None:
        return next((slot for slot in self.slots if slot.state.ends_ask), None)

    @property
    def open_slots(self) -> tuple[Slot, ...]:
        return tuple(slot for slot in self.slots if not slot.state.is_settled)


@dataclass(frozen=True)
class Settled:
    frame: Frame


# NeedsClarification and CannotServe are also an ask's outcome in Evidence, so they
# answer the questions every outcome does.
@dataclass(frozen=True)
class NeedsClarification:
    open_slots: tuple[Slot, ...]
    is_served = False
    is_unanswered = False
    has_provider_failure = False


@dataclass(frozen=True)
class CannotServe:
    slot: Slot
    is_served = False
    is_unanswered = True
    has_provider_failure = False


# Closed on purpose, like the states: callers match on these three.
type Settlement = Settled | NeedsClarification | CannotServe
