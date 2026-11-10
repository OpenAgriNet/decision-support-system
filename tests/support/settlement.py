from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.settlement.models import Frame, Settled, Settlement


def settles_every_ask(intent: Intent) -> tuple[Settlement, ...]:
    """For a test that is not about settlement: every ask goes on."""

    return tuple(Settled(Frame(slots=())) for _ in intent.asks)
