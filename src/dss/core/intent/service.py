"""Intent recognition.

The confidence floor is a business rule, so it lives here rather than in a
prompt or an adapter. `None` means "not confident enough to act" and is a real
answer — never a fabricated intent, because everything downstream would then be
routing on a reading nobody trusts.
"""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.shared.llm import LLM
from dss.core.shared.models import UserTurn

CONFIDENCE_FLOOR = 0.6

# STUB(#83): a real prompt carries the taxonomy, the examples, and the
# adopter's domain extensions.
_SYSTEM_PROMPT = "Classify the agricultural question into a domain and action type."


async def recognise_intent(turn: UserTurn, *, llm: LLM) -> Intent | None:
    """Read the capability need off a turn, or `None` below the floor."""

    intent = await llm.structured(
        system_prompt=_SYSTEM_PROMPT,
        user_query=turn.query,
        schema=Intent,
    )
    return intent if intent.confidence >= CONFIDENCE_FLOOR else None
