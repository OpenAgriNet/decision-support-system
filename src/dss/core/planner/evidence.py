"""Builds ``Evidence`` from the answers the loop's tools accumulated.

Two consumers, two shapes: the model reads rendered markdown and cites it,
while ``Evidence`` carries typed data for the composer downstream. The
``select`` tool feeds both — it returns markdown and accumulates the raw
``DiscoveredAnswer`` on the agent's deps. This assembles the second.

``Result.data`` is a capability's ``resourceAttributes`` verbatim. Nothing
here reads, reshapes or validates them: each pack publishes its own shape
(``prices``, ``recommendations``, ``parameters``, ``services``) and they have
nothing in common past the JSON-LD envelope. Interpretation belongs to the
model and the composer, both of which read prose. So a capability the DSS has
never seen assembles with no code change.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace

from dss.core.forms.models import FormDefinition
from dss.core.intent.models import AmbiguousPlace, Ask, Intent, UnresolvedPlace
from dss.core.planner.models import (
    AskEvidence,
    AskOutcome,
    Attempted,
    AwaitingInput,
    Evidence,
    Failure,
    Result,
    Source,
    SourceKind,
)
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.settlement.models import Settled, Settlement


def place_failures(
    intent: Intent, *, place_optional: Collection[int] = ()
) -> list[Failure]:
    """One failure per ask whose place has nowhere to search.

    Recorded before the planner runs, so the farmer hears about the ask even
    when the model never tries to call anything for it.

    An ask with no place at all fails too, unless `place_optional` names it:
    the pack serves it from nowhere (`core/planner/place.py`).
    """

    failures: list[Failure] = []
    for index, ask in enumerate(intent.asks):
        if isinstance(ask.place, UnresolvedPlace):
            reason = f"{ask.place.unresolved_name}: place not found"
        elif isinstance(ask.place, AmbiguousPlace):
            reason = f"{ask.place.unresolved_name}: matches several places"
        elif ask.place is None and index not in place_optional:
            reason = "no place given, and this needs one"
        else:
            continue
        failures.append(
            Failure(ask_index=index, capability=None, reason=reason, retryable=False)
        )
    return failures


def assemble_evidence(
    raw_answers: Sequence[tuple[int, DiscoveredAnswer]],
    *,
    intent: Intent,
    direct_answers: Mapping[int, tuple[DiscoveredAnswer, ...]] | None = None,
    failures: Sequence[Failure] = (),
    awaiting: Mapping[int, FormDefinition] | None = None,
) -> Evidence:
    """Turn everything the turn gathered into ``Evidence``.

    Two sources of answer, both ``DiscoveredAnswer``, so both assemble the
    same way:

    - ``raw_answers`` — what the loop's ``select`` calls returned.
    - ``direct_answers`` — ``DiscoveryResult.answers``, values the catalog
      already holds. These never appear in ``raw_answers``, because a Direct
      resource needs no call. Leaving them out meant an ask the catalog had
      already answered reached the planner's prompt and then vanished: the
      planner is told not to answer, and the composer never saw it.

    ``failures`` are calls that errored after the adapter's retries, and asks
    whose place left nowhere to search. They ride alongside the results so the
    composer can say "we could not reach Agmarknet" rather than "nobody serves
    this" — the same empty result, two different things to tell a farmer.

    Sources are numbered ``"1"``, ``"2"``, … in first-seen order, one per
    ``(provider, originator)`` pair:

    - A source is named by who authored the data, not who served it — a
      provider relaying IMD cites IMD, falling back to its own name.
    - Keyed on the pair, so one provider relaying two originators is two
      sources; ``sourceId`` is provider-scoped, so two providers relaying one
      originator stay two.
    - ``kind`` stays ``PROVIDER``: it says how the DSS reached the fact, which
      is not what ``name`` says.
    """

    source_id_by_origin: dict[tuple[str, str | None], str] = {}
    sources: list[Source] = []
    results: list[Result] = []

    direct = [
        (ask_index, answer)
        for ask_index, ask_answers in (direct_answers or {}).items()
        for answer in ask_answers
    ]

    for ask_index, answer in [*direct, *raw_answers]:
        origin = (answer.provider_id, answer.source_id)
        source_id = source_id_by_origin.get(origin)
        if source_id is None:
            source_id = str(len(sources) + 1)
            source_id_by_origin[origin] = source_id
            sources.append(
                Source(
                    id=source_id,
                    name=answer.source_name or answer.provider_name,
                    kind=SourceKind.PROVIDER,
                    url=answer.source_url,
                )
            )
        results.append(
            Result(
                ask_index=ask_index,
                source_id=source_id,
                data=dict(answer.attributes),
            )
        )

    return Evidence(
        sources=tuple(sources),
        ask_evidences=group_by_ask(
            intent.asks, results=results, failures=failures, awaiting=awaiting
        ),
    )


def group_by_ask(
    asks: Sequence[Ask],
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[int, FormDefinition] | None = None,
) -> tuple[AskEvidence, ...]:
    """One entry per ask, in ask order: the form it awaits, or else its
    results and failures.

    Raises ``ValueError`` for a part naming an ask the turn does not have, or
    a form ask that was also attempted.
    """

    awaiting = awaiting or {}
    _reject_stray_ask_indexes(
        len(asks), results=results, failures=failures, awaiting=awaiting
    )
    _reject_attempted_form_asks(results=results, failures=failures, awaiting=awaiting)
    return tuple(
        AskEvidence(
            ask=ask,
            outcome=_outcome_for(
                index, results=results, failures=failures, awaiting=awaiting
            ),
        )
        for index, ask in enumerate(asks)
    )


def merge_unsettled(
    intent: Intent, settlements: Sequence[Settlement], *, settled: Evidence
) -> Evidence:
    """Every ask in the farmer's order. A settled ask gets its evidence.
    An unsettled ask gets its settlement.

    Raises ValueError if the settled evidence does not match the settled asks.
    """

    settled_count = sum(isinstance(s, Settled) for s in settlements)
    if len(settled.ask_evidences) != settled_count:
        raise ValueError(
            f"{len(settled.ask_evidences)} evidence entries for "
            f"{settled_count} settled asks"
        )
    settled_evidences = iter(settled.ask_evidences)
    ask_evidences = tuple(
        next(settled_evidences)
        if isinstance(settlement, Settled)
        else AskEvidence(ask=ask, outcome=settlement)
        for ask, settlement in zip(intent.asks, settlements, strict=True)
    )
    return replace(settled, ask_evidences=ask_evidences)


def _outcome_for(
    ask_index: int,
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[int, FormDefinition],
) -> AskOutcome:
    form = awaiting.get(ask_index)
    if form is not None:
        return AwaitingInput(form=form)
    return Attempted(
        results=tuple(result for result in results if result.ask_index == ask_index),
        failures=tuple(
            failure for failure in failures if failure.ask_index == ask_index
        ),
    )


def _reject_stray_ask_indexes(
    ask_count: int,
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[int, FormDefinition],
) -> None:
    # Every producer keeps the index in range, so a stray one is a bug. Dropping
    # the part instead would quietly turn the turn into no_match.
    named = [*(part.ask_index for part in (*results, *failures)), *awaiting]
    stray = [index for index in named if not 0 <= index < ask_count]
    if stray:
        raise ValueError(f"ask {stray[0]} is not one of the turn's {ask_count} asks")


def _reject_attempted_form_asks(
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[int, FormDefinition],
) -> None:
    # An ask has one outcome. A form ask with a result would lose one of them.
    attempted = {part.ask_index for part in (*results, *failures)}
    both = sorted(attempted & awaiting.keys())
    if both:
        raise ValueError(f"ask {both[0]} awaits a form and was also attempted")
