from __future__ import annotations

from collections.abc import Sequence

from dss.core.intent.models import Ask
from dss.core.settlement.models import AskSettlement, Frame, Settled, Settlement
from dss.orchestration.settlement import SettleAsks


def settles_every_ask(asks: Sequence[Ask]) -> tuple[AskSettlement, ...]:
    """For a test that is not about settlement: every ask goes on."""

    return tuple(AskSettlement(ask, Settled(Frame(slots=()))) for ask in asks)


def settles_with(*settlements: Settlement) -> SettleAsks:
    """Gives the turn's asks these settlements, in ask order."""

    def settle(asks: Sequence[Ask]) -> tuple[AskSettlement, ...]:
        return tuple(
            AskSettlement(ask, settlement)
            for ask, settlement in zip(asks, settlements, strict=True)
        )

    return settle
