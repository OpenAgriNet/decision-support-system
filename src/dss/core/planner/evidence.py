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

from collections.abc import Mapping, Sequence
from dataclasses import replace

from dss.core.forms.models import FormDefinition
from dss.core.intent.models import Ask, AskId
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
from dss.core.provider_discovery.models import AnswerOrigin, DiscoveredAnswer
from dss.core.settlement.models import AskSettlement, Settled


def assemble_evidence(
    raw_answers: Sequence[tuple[AskId, DiscoveredAnswer]],
    *,
    asks: Sequence[Ask],
    direct_answers: Sequence[tuple[AskId, DiscoveredAnswer]] = (),
    failures: Sequence[Failure] = (),
    awaiting: Mapping[AskId, FormDefinition] | None = None,
) -> Evidence:
    """Sources are numbered from 1 in first-seen order, direct answers first.

    ``direct_answers`` must be passed in: a Direct resource needs no call, so
    it never appears in ``raw_answers``."""

    source_by_origin: dict[AnswerOrigin, Source] = {}
    results: list[Result] = []

    for ask_id, answer in [*direct_answers, *raw_answers]:
        origin = answer.origin
        if origin not in source_by_origin:
            source_by_origin[origin] = _source_of(
                answer, number=len(source_by_origin) + 1
            )
        results.append(
            Result(
                ask_id=ask_id,
                source=source_by_origin[origin],
                data=dict(answer.attributes),
            )
        )

    return Evidence(
        ask_evidences=group_by_ask(
            asks, results=results, failures=failures, awaiting=awaiting
        ),
    )


def _source_of(answer: DiscoveredAnswer, *, number: int) -> Source:
    return Source(
        id=str(number),
        name=answer.source_name or answer.provider_name,
        kind=SourceKind.PROVIDER,
        url=answer.source_url,
    )


def group_by_ask(
    asks: Sequence[Ask],
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[AskId, FormDefinition] | None = None,
) -> tuple[AskEvidence, ...]:
    """One entry per ask, in ask order: the form it awaits, or else its
    results and failures.

    Raises ``ValueError`` for a part naming an ask the turn does not have, or
    a form ask that was also attempted.
    """

    awaiting = awaiting or {}
    _reject_stray_ask_ids(asks, results=results, failures=failures, awaiting=awaiting)
    _reject_attempted_form_asks(results=results, failures=failures, awaiting=awaiting)
    return tuple(
        AskEvidence(
            ask=ask,
            outcome=_outcome_for(
                ask.id, results=results, failures=failures, awaiting=awaiting
            ),
        )
        for ask in asks
    )


def merge_unsettled(
    settlements: Sequence[AskSettlement], *, settled: Evidence
) -> Evidence:
    """Every ask in the farmer's order. A settled ask gets its evidence.
    An unsettled ask gets its settlement.

    Raises ValueError if a settled ask has no evidence.
    """

    evidence_by_ask_id = {
        ask_evidence.ask.id: ask_evidence for ask_evidence in settled.ask_evidences
    }
    ask_evidences = tuple(
        _evidence_for(ask_settlement, evidence_by_ask_id)
        for ask_settlement in settlements
    )
    return replace(settled, ask_evidences=ask_evidences)


def _evidence_for(
    ask_settlement: AskSettlement, evidence_by_ask_id: Mapping[AskId, AskEvidence]
) -> AskEvidence:
    ask, settlement = ask_settlement.ask, ask_settlement.settlement
    if not isinstance(settlement, Settled):
        return AskEvidence(ask=ask, outcome=settlement)
    evidence = evidence_by_ask_id.get(ask.id)
    if evidence is None:
        raise ValueError(f"no evidence for settled ask {ask.id}")
    return evidence


def _outcome_for(
    ask_id: AskId,
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[AskId, FormDefinition],
) -> AskOutcome:
    form = awaiting.get(ask_id)
    if form is not None:
        return AwaitingInput(form=form)
    return Attempted(
        results=tuple(result for result in results if result.ask_id == ask_id),
        failures=tuple(failure for failure in failures if failure.ask_id == ask_id),
    )


def _reject_stray_ask_ids(
    asks: Sequence[Ask],
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[AskId, FormDefinition],
) -> None:
    # Every producer names one of the asks it was given, so a stray id is a
    # bug. Dropping the part instead would quietly turn the turn into no_match.
    known = {ask.id for ask in asks}
    named = [*(part.ask_id for part in (*results, *failures)), *awaiting]
    stray = [ask_id for ask_id in named if ask_id not in known]
    if stray:
        raise ValueError(f"ask {stray[0]} is not one of the asks {sorted(known)}")


def _reject_attempted_form_asks(
    *,
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[AskId, FormDefinition],
) -> None:
    # An ask has one outcome. A form ask with a result would lose one of them.
    attempted = {part.ask_id for part in (*results, *failures)}
    both = sorted(attempted & awaiting.keys())
    if both:
        raise ValueError(f"ask {both[0]} awaits a form and was also attempted")
