"""Response composition — shaping the written answer for its channel.

Writing the prose is the model's job (`orchestration/compose.py`, which needs
an `LLMProvider`); this module does the deterministic structural shaping that
stays in `core/`: turning the composer's text plus the planner's `Evidence`
into the `ComposedAnswer` the transport streams, and the fixed no-match reply.
"""

from __future__ import annotations

from dss.core.channel.models import ComposedAnswer
from dss.core.planner.models import Evidence
from dss.core.planner.models import Source as EvidenceSource
from dss.core.shared.models import Source, SourceKind, TextBlock

NO_MATCH_TEXT = (
    "I could not find a way to help with that. I can assist with agriculture "
    "and livestock questions."
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
    """

    sources = tuple(_to_wire_source(source) for source in evidence.sources)
    block = TextBlock(text=text, source_ids=tuple(source.id for source in sources))
    return ComposedAnswer(content=(block,), sources=sources)
