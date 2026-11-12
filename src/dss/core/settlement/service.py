from __future__ import annotations

from collections.abc import Callable

from dss.core.intent.models import Ask, Intent
from dss.core.settlement.ask_slots import frame_for
from dss.core.settlement.models import (
    AskRequirements,
    CannotServe,
    Frame,
    NeedsClarification,
    Settled,
    Settlement,
)


def settle_asks(
    intent: Intent, *, requirements_for: Callable[[Ask], AskRequirements]
) -> tuple[Settlement, ...]:
    """One settlement per ask, in ask order."""

    return tuple(settle(frame_for(ask, requirements_for(ask))) for ask in intent.asks)


def settle(frame: Frame) -> Settlement:
    ending_slot = frame.ending_slot
    if ending_slot is not None:
        return CannotServe(ending_slot)
    if frame.open_slots:
        return NeedsClarification(frame.open_slots)
    return Settled(frame)
