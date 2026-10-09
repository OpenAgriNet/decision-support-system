"""Response composition — shaping the written answer for its channel.

Writing the prose is the model's job (`core/stream_response/service.py`); this
module does the deterministic structural shaping that stays in `core/`: turning
the composer's text plus the planner's `Evidence` into the `ComposedAnswer` the
transport streams, and the fixed no-match reply.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence

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
    """What the farmer reads when moderation finds the ask out of scope and
    gives no message of its own.

    Not for an ask nobody on the network serves: that is
    `answer_for_unserved_asks`, which names the place.

    Deterministic, so it needs no model. The real version writes in
    `target_lang` and for the channel.
    """

    return ComposedAnswer(content=(TextBlock(text=NO_MATCH_TEXT),))


def answer_for_unserved_asks(
    asks: Sequence[Ask], text: ClarificationText
) -> ComposedAnswer:
    """What the farmer reads when the ask is understood but nobody on the
    network serves it. Not `no_match_answer`: that one reads as out of scope.

    The reply names the place, never the schema, so a new schema needs no
    new text.
    """

    lines = [
        text.no_provider_for.format(place=ask.place.name)
        if ask.place.name
        else text.no_provider
        for ask in asks
        if isinstance(ask.place, ResolvedPlace)
    ]
    # Two asks on one place are one thing for the farmer to act on.
    unique = dict.fromkeys(lines)
    return ComposedAnswer(content=(TextBlock(text="\n".join(unique)),))


def _standout_part(within: tuple[str, ...], others: list[tuple[str, ...]]) -> str:
    """The first part of the chain where no other choice matches it any more.
    With no other choice, the place directly above it: "Chatra", not "India"."""

    if not others:
        return within[-1] if within else ""
    for depth in range(len(within)):
        if not any(other[: depth + 1] == within[: depth + 1] for other in others):
            return within[depth]
    return within[-1] if within else ""


def _candidate_lines(candidates: Sequence[AreaMatch]) -> list[str]:
    """Each choice shows the part of its chain that sets it apart: two Ashtis
    in Maharashtra read "Wardha" and "Beed", not the state twice.

    Numbered, one line per choice, so a later turn can read the list back
    from history and pick the farmer's reply against it rather than guess.
    """

    chains = [candidate.within for candidate in candidates]
    return [
        f"{number}. {candidate.name}, "
        f"{_standout_part(candidate.within, chains[: number - 1] + chains[number:])}"
        for number, candidate in enumerate(candidates, start=1)
    ]


def _group_lines(
    name: str, candidates: Sequence[AreaMatch], text: ClarificationText
) -> list[str]:
    """One line per group, at the first level of the chains where they differ.
    The level is never named: it is a state in India and a county in Kenya.
    Past `max_choices` groups the rest are cut and the farmer is told how to
    reach them."""

    for depth in range(max(len(candidate.within) for candidate in candidates)):
        parts = list(
            dict.fromkeys(
                candidate.within[depth]
                for candidate in candidates
                if len(candidate.within) > depth
            )
        )
        if len(parts) > 1:
            shown = parts[: text.max_choices]
            lines = [
                f"{number}. {name}, {part}" for number, part in enumerate(shown, 1)
            ]
            if len(shown) < len(parts):
                lines.append(text.more_places_hint)
            return lines
    return _candidate_lines(candidates)


def _ambiguous_lines(place: AmbiguousPlace, text: ClarificationText) -> list[str]:
    name = place.unresolved_name
    if len(place.candidates) > text.max_choices:
        return [
            text.grouped_place_header.format(name=name),
            *_group_lines(name, place.candidates, text),
        ]
    return [
        text.ambiguous_place_header.format(name=name),
        *_candidate_lines(place.candidates),
    ]


def _not_found_line(name: str, text: ClarificationText) -> str:
    """ "Aurangabad, Maharashtra" reads as "I could not find Aurangabad in
    Maharashtra": a farmer may not read the comma as "in"."""

    place, comma, part = name.rpartition(",")
    if not comma:
        return text.unknown_place.format(name=name)
    return text.unknown_place_in.format(name=place.strip(), part=part.strip())


def question_for_unplaced_asks(
    asks: Sequence[Ask],
    text: ClarificationText,
    *,
    place_optional: Collection[int] = (),
) -> str | None:
    """The closing block for the asks whose place is still missing, on a turn
    where other asks were answered: "which one?" for a name that matched
    several, the plain question for an ask that named nowhere and needed a
    place. `None` if nothing is missing.

    `place_optional` means the same as in `answer_for_unplaced_asks`.
    """

    lines: list[str] = []
    reported: list[AmbiguousPlace] = []
    needs_place = False
    for index, ask in enumerate(asks):
        if isinstance(ask.place, AmbiguousPlace) and ask.place not in reported:
            reported.append(ask.place)
            lines.extend(_ambiguous_lines(ask.place, text))
        elif ask.place is None and index not in place_optional:
            needs_place = True
    if needs_place:
        lines.append(text.needs_place)
    return "\n".join(lines) or None


def answer_for_unplaced_asks(
    asks: Sequence[Ask],
    text: ClarificationText,
    *,
    place_optional: Collection[int] = (),
) -> ComposedAnswer | None:
    """What the farmer reads when no ask has a place it needs. `None` if any
    ask can be answered; the rest travel on as failures, so the answerable
    ones still get answered.

    `place_optional` names the asks (by position) that have no place and need
    none — the pack serves them from nowhere (`core/planner/place.py`). They
    count as answerable, not as missing a place.

    Reports every failing ask, not just one, so fixing all of them takes one
    reply. Plain `None` asks share one question, asked once.
    """

    if any(
        isinstance(ask.place, ResolvedPlace)
        or (ask.place is None and index in place_optional)
        for index, ask in enumerate(asks)
    ):
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
            lines.extend(_ambiguous_lines(ask.place, text))
        elif isinstance(ask.place, UnresolvedPlace) and ask.place.region:
            lines.append(text.region_place.format(name=ask.place.region))
        elif isinstance(ask.place, UnresolvedPlace):
            lines.append(_not_found_line(ask.place.unresolved_name, text))
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
