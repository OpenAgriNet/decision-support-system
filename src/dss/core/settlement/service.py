from __future__ import annotations

from collections.abc import Callable, Sequence

from dss.core.intent.models import Ask
from dss.core.settlement.ask_slots import frame_for
from dss.core.settlement.models import (
    AskRequirements,
    AskSettlement,
    CannotServe,
    Frame,
    NeedsClarification,
    Settled,
    Settlement,
)


def settle_asks(
    asks: Sequence[Ask], *, requirements_for: Callable[[Ask], AskRequirements]
) -> tuple[AskSettlement, ...]:
    """One per ask, in ask order."""

    return tuple(
        AskSettlement(ask, settle(frame_for(ask, requirements_for(ask))))
        for ask in asks
    )


def settle(frame: Frame) -> Settlement:
    ending_slot = frame.ending_slot
    if ending_slot is not None:
        return CannotServe(ending_slot)
    if frame.open_slots:
        return NeedsClarification(frame.open_slots)
    return Settled(frame)
