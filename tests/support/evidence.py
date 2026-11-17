from __future__ import annotations

from collections.abc import Mapping, Sequence

from dss.core.forms.models import FormDefinition
from dss.core.intent.models import Ask
from dss.core.planner.evidence import group_by_ask
from dss.core.planner.models import (
    AskEvidence,
    Attempted,
    Evidence,
    Failure,
    Result,
    Source,
)
from tests.support.asks import an_ask, numbered

# For a test that does not care which ask its results answer.
ANY_ASK = an_ask()


def build_evidence(
    *,
    sources: Sequence[Source] = (),
    results: Sequence[Result] = (),
    failures: Sequence[Failure] = (),
    awaiting: Mapping[int, FormDefinition] | None = None,
    asks: Sequence[Ask] | None = None,
) -> Evidence:
    """Evidence as `assemble_evidence` would shape it, from its parts. With no
    `asks`, every ask id the parts name stands for an `ANY_ASK` with that id."""

    awaiting = awaiting or {}
    if asks is None:
        asks = numbered(*(ANY_ASK,) * _count_asks_named(results, failures, awaiting))
    return Evidence(
        sources=tuple(sources),
        ask_evidences=group_by_ask(
            asks, results=results, failures=failures, awaiting=awaiting
        ),
    )


def attempted(ask_evidence: AskEvidence) -> Attempted:
    """The ask's outcome, failing the test if it is not an attempted one."""

    assert isinstance(ask_evidence.outcome, Attempted), ask_evidence.outcome
    return ask_evidence.outcome


def _count_asks_named(
    results: Sequence[Result],
    failures: Sequence[Failure],
    awaiting: Mapping[int, FormDefinition],
) -> int:
    indexes = [*(part.ask_id for part in (*results, *failures)), *awaiting]
    return max(indexes, default=-1) + 1
