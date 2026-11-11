"""Response composition — shaping the written answer for its channel.

Writing the prose is the model's job (`core/stream_response/service.py`); this
module does the deterministic structural shaping that stays in `core/`: turning
the composer's text plus the planner's `Evidence` into the `ComposedAnswer` the
transport streams, and the fixed no-match reply.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.intent.models import Ask, ResolvedPlace
from dss.core.planner.models import AskOutcome, Evidence
from dss.core.planner.models import Source as EvidenceSource
from dss.core.settlement.models import Ambiguous, AskSlot, NeedsClarification, Slot
from dss.core.shared.models import Source, SourceKind, TextBlock
from dss.core.stream_response.citations import CitationNumbers
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

    Deterministic, so it needs no model — like every string this module
    builds, it is English here and rendered in the turn's `target_lang` at the
    orchestrator seam (`core/channel/localize.py`, ADR-0018).
    """

    return ComposedAnswer(content=(TextBlock(text=NO_MATCH_TEXT),))


def answer_for_unserved_asks(
    asks: Sequence[Ask], text: ClarificationText
) -> ComposedAnswer:
    """What the farmer reads when the ask is understood but nobody on the
    network serves it. Not `no_match_answer`: that one reads as out of scope.

    The reply names the place when the ask has one, never the schema, so a
    new schema needs no new text.
    """

    lines = [_no_provider_line(ask, text) for ask in asks]
    # Two asks on one place are one thing for the farmer to act on.
    unique = dict.fromkeys(lines)
    return ComposedAnswer(content=(TextBlock(text="\n".join(unique)),))


def _no_provider_line(ask: Ask, text: ClarificationText) -> str:
    if isinstance(ask.place, ResolvedPlace) and ask.place.name:
        return text.no_provider_for.format(place=ask.place.name)
    return text.no_provider


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


def pick_lists(evidence: Evidence, text: ClarificationText) -> tuple[TextBlock, ...]:
    """The numbered choices for each pick the farmer has to make. Asks stuck
    on the same choices share one list. The composer asks the question."""

    pick_per_ask = (_place_pick(entry.outcome) for entry in evidence.ask_evidences)
    return tuple(
        TextBlock(text="\n".join(_place_choice_lines(pick, text)))
        for pick in _distinct(pick_per_ask)
        if pick is not None
    )


def _distinct[T](items: Iterable[T]) -> list[T]:
    # By equality, not a set: a place's geometry holds a list and cannot hash.
    distinct: list[T] = []
    for item in items:
        if item not in distinct:
            distinct.append(item)
    return distinct


def _place_pick(outcome: AskOutcome) -> Ambiguous[AreaMatch] | None:
    """The place choices the ask waits on first, if that is what it waits on."""

    match outcome:
        case NeedsClarification(
            open_slots=(Slot(name=AskSlot.PLACE, state=Ambiguous() as pick), *_)
        ):
            return pick
    return None


def _place_choice_lines(
    pick: Ambiguous[AreaMatch], text: ClarificationText
) -> list[str]:
    if len(pick.choices) > text.max_choices:
        # The choices may each have their own name. Only the typed one fits
        # every group.
        return _group_lines(pick.value, pick.choices, text)
    return _candidate_lines(pick.choices)


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
    """Shape the composer's prose and the sources it used into the answer the
    transport streams.

    Only cited sources are listed, renumbered from [1] in first-cited order.
    """

    numbers = CitationNumbers(source.id for source in evidence.sources)
    text = numbers.feed(text) + numbers.flush()
    by_id = {source.id: source for source in evidence.sources}
    sources = tuple(
        _to_wire_source(by_id[source_id]).model_copy(update={"id": number})
        for source_id, number in numbers.cited.items()
    )
    block = TextBlock(text=text, source_ids=tuple(source.id for source in sources))
    return ComposedAnswer(content=(block,), sources=sources)
