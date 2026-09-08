"""The throwaway composer — ``Evidence`` in, an answer for the farmer out.

A stand-in for the design's Response Composer (§6.8). It is a *separate* LLM
call from the planner's loop, deliberately: `Evidence` stays the seam, so the
real composer replaces this function without touching the planner's output.

What it does not do, and the real one will: stream claims, carry a `Claim`
tuple with per-sentence source ids, shape for voice/SMS, or say what
moderation refused.

Here rather than in ``core/`` because it calls Pydantic AI directly —
``LLMProvider`` only offers ``structured()``, and prose is not a schema.
Adding a ``text()`` method to that port and moving this to ``core/`` is the
follow-up.
"""

from __future__ import annotations

import json
from typing import Protocol

from pydantic_ai import Agent
from pydantic_ai.models import Model

from dss.core.planner.markers import (
    QUESTION,
    RETRIEVED_DATA,
    wrap_as_data,
)
from dss.core.planner.models import Evidence, Identity
from dss.core.shared.models import UserTurn

_SYSTEM_PROMPT = """You are {name}, {persona}

{boundaries}

Write the answer a farmer will read. Use only the retrieved data below —
never your own knowledge, and never a number the data does not contain.

- Answer the question that was asked, leading with what it asked for.
- Plain words, short sentences. A farmer is reading this, not an analyst.
- Give values the way a person says them: "2,200 Rs", not "modal: 2200".
- Cite a source with its number in square brackets, like [1].
- Reply in {target_lang}.

If the data does not answer the question, say plainly that you could not
find it. Do not fill the gap with a guess — a wrong price costs a farmer
money.

If a line says a provider could not be reached, say the information was not
available right now and could be worth asking again. That is different from
nobody having the answer — do not turn one into the other.
"""


class Compose(Protocol):
    async def __call__(self, evidence: Evidence, *, turn: UserTurn) -> str: ...


def _render_evidence(evidence: Evidence) -> str:
    """Lay out the sources and their values for the model.

    ``Result.data`` is a provider's ``resourceAttributes`` verbatim — each
    pack has its own shape, so this does not try to interpret them. Turning
    ``{"prices": {"modal": 2200}}`` into "2,200 Rs" is the model's job; this
    only has to make the values legible and say which source each came from.
    """

    name_by_id = {source.id: source.name for source in evidence.sources}
    blocks = [
        f"[{result.source_id}] {name_by_id.get(result.source_id, 'unknown')}\n"
        f"{json.dumps(result.data, indent=2, ensure_ascii=False)}"
        for result in evidence.results
    ]
    blocks.extend(_render_failures(evidence))

    if not blocks:
        return "Nothing was retrieved."
    return "\n\n".join(blocks)


def _render_failures(evidence: Evidence) -> list[str]:
    """Calls that did not answer, and why.

    "We could not reach Agmarknet" and "nobody serves this" are the same
    empty result but very different things to tell a farmer. Without this the
    composer saw only ``results`` and rendered both as "Nothing was
    retrieved.", which is the confusion ``Evidence.failed`` was added to
    prevent.
    """

    return [
        f"Could not reach a provider for {failure.capability}: {failure.reason}"
        for failure in evidence.failed
    ]


def build_compose(*, identity: Identity, model: Model | str) -> Compose:
    """Bind the identity and model once; return the per-turn callable."""

    async def compose(evidence: Evidence, *, turn: UserTurn) -> str:
        agent: Agent[None, str] = Agent(
            model,
            system_prompt=_SYSTEM_PROMPT.format(
                name=identity.name,
                persona=identity.persona,
                boundaries=identity.boundaries,
                target_lang=turn.target_lang,
            ),
        )
        # The question and the provider's values, wrapped as data — a
        # provider's text is third-party and must never read as instructions,
        # and `wrap_as_data` stops either from closing its block early.
        user_message = (
            wrap_as_data(turn.enriched_query, QUESTION)
            + "\n\n"
            + wrap_as_data(_render_evidence(evidence), RETRIEVED_DATA)
        )
        result = await agent.run(user_message)
        return result.output

    return compose
