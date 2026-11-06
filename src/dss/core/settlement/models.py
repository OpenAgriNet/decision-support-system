from __future__ import annotations

from dataclasses import dataclass
from typing import NewType, Protocol

SlotName = NewType("SlotName", str)


class SlotState(Protocol):
    """`settle` reads only these, never the state's class, so a new state
    needs no change to `settle`."""

    @property
    def is_settled(self) -> bool: ...

    @property
    def ends_ask(self) -> bool: ...


@dataclass(frozen=True)
class Filled[T]:
    value: T
    is_settled = True
    ends_ask = False


@dataclass(frozen=True)
class Missing:
    is_settled = False
    ends_ask = False


@dataclass(frozen=True)
class Unservable:
    reason: str
    is_settled = False
    ends_ask = True


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


@dataclass(frozen=True)
class NeedsAnswer:
    open_slots: tuple[Slot, ...]


@dataclass(frozen=True)
class CannotServe:
    slot: Slot


# Closed on purpose: callers match on these three, and a fourth is a design
# change, unlike a new kind of slot state.
type Settlement = Settled | NeedsAnswer | CannotServe
