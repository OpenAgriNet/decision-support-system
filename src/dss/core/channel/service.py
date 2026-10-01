"""Response composition — shaping the written answer for its channel.

Writing the prose is the model's job (`core/stream_response/service.py`); this
module does the deterministic structural shaping that stays in `core/`: turning
the composer's text plus the planner's `Evidence` into the `ComposedAnswer` the
transport streams, and the fixed no-match reply.
"""

from __future__ import annotations

import re

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


# Asks for a *district* by name, not "where are you from?". The area lookup
# holds districts only, so an open question invites a village or a city and the
# next turn fails to resolve for the same reason.
NEEDS_DISTRICT_TEXT = (
    "Which district are you in? I need it to find information for your area."
)


def needs_district_answer() -> ComposedAnswer:
    """What the farmer reads when the turn has no location to search around.

    Deterministic like `no_match_answer`, and for the same reason: asking a
    fixed question needs no model. Shares that function's caveat — the real
    version writes in `target_lang` and for the channel.
    """

    return ComposedAnswer(content=(TextBlock(text=NEEDS_DISTRICT_TEXT),))


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


_CITATION = re.compile(r"\[(\d+)\]")


def answer_from_evidence(text: str, evidence: Evidence) -> ComposedAnswer:
    """Shape the composer's prose and the sources it used into the answer the
    transport streams.

    The composer writes one block of text and cites sources inline as `[1]`,
    `[2]` — the numbers are the `Source.id`s `assemble_evidence` assigned.

    Only the sources it actually cited are listed. A question is answered from
    one source, so the others were read and rejected; listing them claims a
    provenance the answer does not have, and a farmer who opens one finds a
    document the advice never came from. A reply answering two questions cites
    two sources and keeps both — the rule is per question.

    A number naming no listed source is dropped: a citation marker pointing at
    nothing is worse than no marker. If the prose cites nothing at all, nothing
    is listed, because there is no way to tell which source it rested on.

    The prose stays a single block (sub-sentence citation spans are a
    follow-up), so the block carries every id cited anywhere in it.
    """

    cited = set(_CITATION.findall(text))
    # Walked in evidence order rather than the order they were cited, so the
    # numbers a farmer reads count up the page. A cited number that names no
    # source never matches, which is how an invented one is dropped.
    sources = tuple(
        _to_wire_source(source) for source in evidence.sources if source.id in cited
    )
    block = TextBlock(text=text, source_ids=tuple(source.id for source in sources))
    return ComposedAnswer(content=(block,), sources=sources)
