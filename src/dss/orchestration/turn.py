"""The turn coordinator — intent and moderation, run in parallel (ADR-0003).

Intent classification and moderation are independent: moderation judges harm on
the raw query, intent classifies capability need, and neither consumes the
other's output. Running them under ``asyncio.gather`` makes the turn's latency
the slower of the two calls rather than their sum.

Each component is bound to its own model via the ``LLMProvider`` it is handed
(see ``config/settings.py``), so a deployment can point intent and moderation at
different models.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from dss.core.intent.models import Intent
from dss.core.intent.service import classify_intent
from dss.core.moderation.models import (
    ModerationContext,
    ModerationDecision,
    Outcome,
)
from dss.core.moderation.service import moderate
from dss.core.policy.models import Policy
from dss.core.shared.models import UserTurn
from dss.ports.llm import LLMProvider


class TurnResult(BaseModel):
    """Both findings for a turn. Moderation is the gate: on any non-``PROCEED``
    outcome the ``intent`` is blanked to an empty ``Intent()`` — a turn that will
    not proceed surfaces no classification, so a rejected/clarify turn never leaks
    an intent read off text the assistant refused to act on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    decision: ModerationDecision


async def run_turn(
    turn: UserTurn,
    *,
    intent_llm: LLMProvider,
    moderation_llm: LLMProvider,
    policies: Sequence[Policy],
) -> TurnResult:
    """Classify intent and moderate the turn concurrently.

    They run in parallel for latency, but moderation still gates the result: if
    the turn does not proceed, the classified intent is discarded in favour of an
    empty ``Intent()``."""

    intent, decision = await asyncio.gather(
        classify_intent(turn, intent_llm),
        moderate(ModerationContext(turn=turn), policies, moderation_llm),
    )
    if decision.outcome is not Outcome.PROCEED:
        intent = Intent()
    return TurnResult(intent=intent, decision=decision)
