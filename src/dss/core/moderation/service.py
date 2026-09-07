"""Moderation — the gate.

Moderation is an agent (2026-09-03 classification), so the signature takes an
`LLM` and keeps it. The body is STUB(#82): a word list, not a model, so the
reject path is deterministic from the first commit. It is the most obviously
fake thing in this slice, on purpose.
"""

from __future__ import annotations

from dss.core.moderation.models import Outcome, Screening
from dss.core.shared.llm import LLM
from dss.core.shared.models import Cause, UserTurn

# STUB(#82): replaced by the policy evaluator reading the Policies primitive.
_DENY = ("illegal", "illegally", "gold loan", "weapon")

_REFUSAL = "I can only answer agriculture and livestock related questions."


async def screen(turn: UserTurn, *, llm: LLM) -> Screening:
    """Decide whether the turn may proceed."""

    lowered = turn.query.casefold()
    if any(word in lowered for word in _DENY):
        return Screening(
            outcome=Outcome.REJECT,
            cause=Cause.UNSAFE_ILLEGAL,
            message=_REFUSAL,
        )
    return Screening(outcome=Outcome.PROCEED)
