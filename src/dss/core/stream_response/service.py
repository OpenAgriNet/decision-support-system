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
from contextlib import aclosing
from typing import Protocol

from dss.core.channel.prompt import system_prompt, user_prompt
from dss.core.planner.models import Evidence, Identity
from dss.core.shared.models import UserTurn
from dss.ports.llm import LLMProvider


class ComposeStream(Protocol):
    """A per-turn streaming composer, already bound to its identity and model.

    ``def``, not ``async def``: an implementation is an async generator, so
    calling one returns the iterator without awaiting.
    """

    def __call__(self, evidence: Evidence, *, turn: UserTurn) -> AsyncIterator[str]: ...


def build_stream_response(*, identity: Identity, llm: LLMProvider) -> ComposeStream:
    """Bind the identity and the model binding once; return the per-turn
    callable, so the composition root is the only place that names either."""

    def compose_stream(evidence: Evidence, *, turn: UserTurn) -> AsyncIterator[str]:
        return stream_response(evidence, turn=turn, identity=identity, llm=llm)

    return compose_stream


async def stream_response(
    evidence: Evidence,
    *,
    turn: UserTurn,
    identity: Identity,
    llm: LLMProvider,
) -> AsyncIterator[str]:
    """Yield the farmer's answer in the pieces the model writes it in."""

    # `aclosing`, not a bare `async for`. Closing an async generator does not
    # reach the one it is relaying from: `async for` has no `yield from`, so
    # `GeneratorExit` stops here and the model's stream stays suspended until
    # the garbage collector finalises it — holding the HTTP connection to the
    # model open after the farmer has already hung up.
    async with aclosing(
        llm.stream_text(
            system_prompt=system_prompt(identity, turn=turn),
            user_query=user_prompt(evidence, turn=turn),
        )
    ) as pieces:
        async for delta in pieces:
            yield delta
