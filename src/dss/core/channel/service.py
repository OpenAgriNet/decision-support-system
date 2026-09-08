"""Response composition — writing the answer for its channel.

An agent under the 2026-09-03 classification: the signature takes an
`LLMProvider` and keeps it. The body is STUB(#84) — a fixed answer, so the
streaming shape can be exercised before anything can reason.

Writing for the channel happens here because the rewrite needs a language model,
and no language model runs outside the DSS. Farmer-facing wording lives here and
not in the runner, for the same reason.
"""

from __future__ import annotations

from dss.core.channel.models import ComposedAnswer
from dss.core.intent.models import Intent
from dss.core.shared.models import Source, SourceKind, TextBlock, UserTurn
from dss.ports.llm import LLMProvider

# TODO(#84): delete `_SOURCE` and the fixed sentences below with the real
# composer. Sources belong to the evidence a plan gathered, not to this module —
# a hardcoded provider here would silently outlive the stub and start citing a
# source no turn actually consulted.
_SOURCE = Source(
    id="src_1",
    name="Agmarknet",
    kind=SourceKind.PROVIDER,
    url="https://agmarknet.gov.in/",
)

NO_MATCH_TEXT = (
    "I could not find a way to help with that. I can assist with agriculture "
    "and livestock questions."
)


async def compose(
    turn: UserTurn, intent: Intent, *, llm: LLMProvider
) -> ComposedAnswer:
    """Write the answer.

    Two blocks, so the multi-claim stream has something to carry. The real
    composer writes one claim per ask (`intent.asks`) from the evidence a plan
    gathered; this ignores both.
    """

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


def no_match_answer() -> ComposedAnswer:
    """What the farmer reads when nothing could serve the ask.

    Deterministic, so it needs no model. The real version writes in
    `target_lang` and for the channel.
    """

    return ComposedAnswer(content=(TextBlock(text=NO_MATCH_TEXT),))
