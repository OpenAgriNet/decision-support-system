from __future__ import annotations

from dss.core.settlement.models import (
    CannotServe,
    Frame,
    NeedsAnswer,
    Settled,
    Settlement,
)


def settle(frame: Frame) -> Settlement:
    ending_slot = frame.ending_slot
    if ending_slot is not None:
        return CannotServe(ending_slot)
    if frame.open_slots:
        return NeedsAnswer(frame.open_slots)
    return Settled(frame)
