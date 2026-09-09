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
from dss.core.planner.models import Evidence
from dss.core.planner.models import Source as EvidenceSource
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


def _to_wire_source(source: EvidenceSource) -> Source:
    """`Evidence` and the wire both name a `Source`, but they are different
    types living either side of the core: the planner's carries what the loop
    gathered, the wire's is what the transport serialises. Same fields today,
    so this is a straight copy — the `SourceKind` enums share their values."""

    return Source(
        id=source.id,
        name=source.name,
        kind=SourceKind(source.kind.value),
        url=source.url,
    )


def answer_from_evidence(text: str, evidence: Evidence) -> ComposedAnswer:
    """Shape the composer's prose and the evidence's sources into the answer
    the transport streams.

    The composer writes one block of text and cites sources inline as `[1]`,
    `[2]` — the numbers are the `Source.id`s `assemble_evidence` assigned. The
    prose stays a single block (sub-sentence citation spans are a follow-up),
    so every source the turn consulted is attached to it: the mapping renders a
    whole-block annotation per id, and `ComposedAnswer` requires each cited id
    to name a listed source, which holds because both come from `evidence`.

    Writing the prose is the model's job (`orchestration/compose.py`); this
    only does the structural shaping, so it stays deterministic in `core/`.
    """

    sources = tuple(_to_wire_source(source) for source in evidence.sources)
    block = TextBlock(text=text, source_ids=tuple(source.id for source in sources))
    return ComposedAnswer(content=(block,), sources=sources)
