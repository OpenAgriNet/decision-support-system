"""Response composition — writing the answer for its channel.

An agent (2026-09-03 classification): the signature takes an `LLM` and keeps it.
The body is STUB(#84) — a fixed answer, so the streaming shape can be exercised
before anything can actually reason.

Writing for the channel happens here because the rewrite needs a language model,
and no language model runs outside the DSS.
"""

from __future__ import annotations

from dss.core.channel.models import ComposedAnswer
from dss.core.intent.models import Intent
from dss.core.shared.models import Source, SourceKind, TextBlock, UserTurn
from dss.ports.llm import LLM

_SOURCE = Source(
    id="src_1",
    name="Agmarknet",
    kind=SourceKind.PROVIDER,
    url="https://agmarknet.gov.in/",
)


async def compose(turn: UserTurn, intent: Intent, *, llm: LLM) -> ComposedAnswer:
    """Write the answer. Two blocks, so the multi-claim stream has something to
    carry."""

    return ComposedAnswer(
        content=(
            TextBlock(
                text="Wheat is trading at Rs 2,275 per quintal at Anand mandi.",
                source_ids=(_SOURCE.id,),
            ),
            TextBlock(
                text="Prices were last updated this morning.",
                source_ids=(_SOURCE.id,),
            ),
        ),
        sources=(_SOURCE,),
    )


def no_match_answer(turn: UserTurn) -> ComposedAnswer:
    """What the farmer reads when nothing could serve the ask.

    Farmer-facing wording lives here, not in the runner: a refusal or a no-match
    still has to be written in the right language for the channel, which is this
    function's job. STUB(#84) — fixed English until composition is real.
    """

    return ComposedAnswer(
        content=(
            TextBlock(
                text=(
                    "I could not find a way to help with that. I can assist with "
                    "agriculture and livestock questions."
                )
            ),
        )
    )
