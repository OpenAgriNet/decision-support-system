from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.settlement.models import AskSettlement, Frame, Settled, Settlement
from dss.orchestration.settlement import SettleAsks


def settles_every_ask(intent: Intent) -> tuple[AskSettlement, ...]:
    """For a test that is not about settlement: every ask goes on."""

    return tuple(AskSettlement(ask, Settled(Frame(slots=()))) for ask in intent.asks)


def settles_with(*settlements: Settlement) -> SettleAsks:
    """Gives the turn's asks these settlements, in ask order."""

    def settle(intent: Intent) -> tuple[AskSettlement, ...]:
        return tuple(
            AskSettlement(ask, settlement)
            for ask, settlement in zip(intent.asks, settlements, strict=True)
        )

    return settle
