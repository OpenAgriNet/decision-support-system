"""The whole-answer composer — ``Evidence`` in, an answer for the farmer out.

A stand-in for the design's Response Composer (§6.8), and now one of two: this
one waits for the model to finish and hands back the answer whole;
``core/stream_response/service.py`` hands the pieces on as they are written.
Both ask the model the same question — the prompt lives in
``core/channel/prompt.py`` precisely so they cannot drift — and they differ in
one thing only: this call may be retried, because nothing has reached the
caller yet.

What neither does, and the real composer will: carry a `Claim` tuple with
per-sentence source ids, shape for voice/SMS, or say what moderation refused.

In ``core/`` rather than ``orchestration/`` because it no longer touches a
vendor SDK: ``LLMProvider.text`` is the seam, so a framework swap does not
reach the prose.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.channel.prompt import system_prompt, user_prompt
from dss.core.planner.models import Evidence, Identity
from dss.core.shared.models import UserTurn
from dss.observability.trace_log import log_external_response
from dss.ports.llm import LLMProvider


class Compose(Protocol):
    async def __call__(self, evidence: Evidence, *, turn: UserTurn) -> str: ...


def build_compose(*, identity: Identity, llm: LLMProvider) -> Compose:
    """Bind the identity and the model binding once; return the per-turn callable.

    Model settings — a warmer temperature than the planner's, its own timeout
    and retries — live on the ``LLMProvider`` the composition root builds
    (ADR-0004), not here.
    """

    async def compose(evidence: Evidence, *, turn: UserTurn) -> str:
        answer = await llm.text(
            system_prompt=system_prompt(identity, turn=turn),
            user_query=user_prompt(evidence, turn=turn),
        )
        log_external_response("llm.composer", turn.transaction_id, body=answer)
        return answer

    return compose
