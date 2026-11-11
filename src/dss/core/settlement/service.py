from __future__ import annotations

from collections.abc import Callable

from dss.core.intent.models import Ask, Intent
from dss.core.settlement.ask_slots import frame_for, needed_slots
from dss.core.settlement.models import (
    AskNeeds,
    CannotServe,
    Frame,
    NeedsClarification,
    Settled,
    Settlement,
)


def settle_asks(
    intent: Intent, *, needs_for: Callable[[Ask], AskNeeds]
) -> tuple[Settlement, ...]:
    """One settlement per ask, in ask order."""

    return tuple(_settle_ask(ask, needs_for(ask)) for ask in intent.asks)


def _settle_ask(ask: Ask, needs: AskNeeds) -> Settlement:
    return settle(frame_for(ask, needed=needed_slots(needs)))


def settle(frame: Frame) -> Settlement:
    ending_slot = frame.ending_slot
    if ending_slot is not None:
        return CannotServe(ending_slot)
    if frame.open_slots:
        return NeedsClarification(frame.open_slots)
    return Settled(frame)
