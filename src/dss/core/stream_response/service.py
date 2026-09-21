"""Stream Response — the composer's answer, handed on as it is written.

The last stage of a turn, and the only one whose output exists before its work
is finished. Everything upstream must complete before it can say anything;
this one has words ready while the model is still writing, and holding them
back is pure waiting for the farmer.

So it yields. A piece goes out the moment the model produces it, and the caller
decides what to do with it — frame it, buffer it, or drop it.

**Pieces are passed on exactly as they arrive.** The model breaks text wherever
it likes: mid-word, mid-number, mid-citation. Nothing here re-splits them onto
tidier boundaries, because the one guarantee everything downstream rests on is
that joining the pieces gives the answer. A "helpful" reshape is how that gets
lost.

**No retry, and none available.** `LLMProvider.stream_text` does not offer one
(`ports/llm.py`): re-issuing after pieces are out would write a *different*
answer over the top of the one the farmer is already reading. A failure part
way through propagates, and the caller reports a failed turn.

The prompt is not built here — it is shared with the whole-answer composer in
`core/channel/prompt.py`, so the two paths cannot drift into asking the model
different questions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dss.core.channel.prompt import system_prompt, user_prompt
from dss.core.planner.models import Evidence, Identity
from dss.core.shared.models import UserTurn
from dss.ports.llm import LLMProvider


async def stream_response(
    evidence: Evidence,
    *,
    turn: UserTurn,
    identity: Identity,
    llm: LLMProvider,
) -> AsyncIterator[str]:
    """Yield the farmer's answer in the pieces the model writes it in."""

    async for delta in llm.stream_text(
        system_prompt=system_prompt(identity, turn=turn),
        user_query=user_prompt(evidence, turn=turn),
    ):
        yield delta
