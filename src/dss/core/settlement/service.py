from __future__ import annotations

from collections.abc import Collection

from dss.core.intent.models import Ask, Intent
from dss.core.provider_discovery.models import CapabilityIndex, NetworkSchemaType
from dss.core.provider_discovery.service import capability_types_for
from dss.core.settlement.ask_slots import frame_for, needed_slots
from dss.core.settlement.models import (
    CannotServe,
    Frame,
    NeedsAnswer,
    Settled,
    Settlement,
)


def settle_asks(
    intent: Intent,
    *,
    index: CapabilityIndex,
    place_bound_types: Collection[NetworkSchemaType],
) -> tuple[Settlement, ...]:
    """One settlement per ask, in ask order."""

    return tuple(
        _settle_ask(ask, index=index, place_bound_types=place_bound_types)
        for ask in intent.asks
    )


def _settle_ask(
    ask: Ask,
    *,
    index: CapabilityIndex,
    place_bound_types: Collection[NetworkSchemaType],
) -> Settlement:
    needed = needed_slots(
        capability_types=capability_types_for(ask, index),
        place_bound_types=place_bound_types,
    )
    return settle(frame_for(ask, needed=needed))


def settle(frame: Frame) -> Settlement:
    ending_slot = frame.ending_slot
    if ending_slot is not None:
        return CannotServe(ending_slot)
    if frame.open_slots:
        return NeedsAnswer(frame.open_slots)
    return Settled(frame)
