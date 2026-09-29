"""Response composition — shaping the written answer for its channel.

Writing the prose is the model's job (`core/stream_response/service.py`); this
module does the deterministic structural shaping that stays in `core/`: turning
the composer's text plus the planner's `Evidence` into the `ComposedAnswer` the
transport streams, and the fixed no-match reply.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.intent.models import AmbiguousPlace, Ask, ResolvedPlace, UnresolvedPlace
from dss.core.planner.models import Evidence
from dss.core.planner.models import Source as EvidenceSource
from dss.core.shared.models import Source, SourceKind, TextBlock
from dss.ports.area_lookup import AreaMatch

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


def needs_place_answer(text: ClarificationText) -> ComposedAnswer:
    """What the farmer reads when nothing in the turn names a place at all.

    Deterministic like `no_match_answer`, and for the same reason: asking a
    fixed question needs no model. Shares that function's caveat — the real
    version writes in `target_lang` and for the channel.
    """

    return ComposedAnswer(content=(TextBlock(text=text.needs_place),))


def unknown_place_answer(name: str, text: ClarificationText) -> ComposedAnswer:
    """What the farmer reads when they named a place the index does not
    carry. A different problem from naming nowhere — they already answered,
    with a name we cannot resolve."""

    return ComposedAnswer(
        content=(TextBlock(text=text.unknown_place.format(name=name)),)
    )


def _standout_part(within: tuple[str, ...], others: list[tuple[str, ...]]) -> str:
    """The first part of the chain where no other choice matches it any more."""

    for depth in range(len(within)):
        if not any(other[: depth + 1] == within[: depth + 1] for other in others):
            return within[depth]
    return within[-1] if within else ""


def _candidate_lines(candidates: Sequence[AreaMatch]) -> list[str]:
    """Each choice shows the part of its chain that sets it apart: two Ashtis
    in Maharashtra read "Wardha" and "Beed", not the state twice."""

    chains = [candidate.within for candidate in candidates]
    return [
        f"{number}. {candidate.name}, "
        f"{_standout_part(candidate.within, chains[: number - 1] + chains[number:])}"
        for number, candidate in enumerate(candidates, start=1)
    ]


def ambiguous_place_answer(
    name: str, candidates: Sequence[AreaMatch], text: ClarificationText
) -> ComposedAnswer:
    """What the farmer reads when a name matches several places.

    A numbered list, one line per candidate, so a later turn can read the
    list back from history and have the farmer's reply picked against it
    rather than guessed.
    """

    lines = [text.ambiguous_place_header.format(name=name)]
    lines.extend(_candidate_lines(candidates))
    return ComposedAnswer(content=(TextBlock(text="\n".join(lines)),))


def answer_for_unplaced_asks(
    asks: Sequence[Ask], text: ClarificationText
) -> ComposedAnswer | None:
    """What the farmer reads when no ask has a place. `None` if any ask
    resolved; the rest travel on as failures, so the resolved ones still get
    answered.

    Reports every failing ask, not just one, so fixing all of them takes one
    reply. Plain `None` asks share one question, asked once.

    Known gap: a plain `None` always reads as "needs a place," even for an
    ask that never needed one (e.g. "how do I grow potatoes").
    """

    if any(isinstance(ask.place, ResolvedPlace) for ask in asks):
        return None

    lines: list[str] = []
    needs_place = False
    # A list, not a set: a place's geometry holds a list, so it cannot hash.
    reported: list[AmbiguousPlace | UnresolvedPlace] = []
    for ask in asks:
        if isinstance(ask.place, AmbiguousPlace | UnresolvedPlace):
            # Two asks failing on one name are one thing for the farmer to fix.
            if ask.place in reported:
                continue
            reported.append(ask.place)
        if isinstance(ask.place, AmbiguousPlace):
            lines.append(
                text.ambiguous_place_header.format(name=ask.place.unresolved_name)
            )
            lines.extend(_candidate_lines(ask.place.candidates))
        elif isinstance(ask.place, UnresolvedPlace):
            lines.append(text.unknown_place.format(name=ask.place.unresolved_name))
        else:
            needs_place = True

    if needs_place:
        lines.append(text.needs_place)
    if not lines:
        return None
    return ComposedAnswer(content=(TextBlock(text="\n".join(lines)),))


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
