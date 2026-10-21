from __future__ import annotations

from collections.abc import Sequence

from dss.core.intent.models import Ask, InteractionType, SubjectCategory
from dss.core.planner.evidence import group_by_ask
from dss.core.planner.models import Evidence, Failure, Result, Source

# For a test that does not care which ask its results answer.
ANY_ASK = Ask(
    subject_categories=SubjectCategory.WEATHER, interaction_type=InteractionType.OBSERVE
)


def build_evidence(
    *,
    sources: Sequence[Source] = (),
    results: Sequence[Result] = (),
    failures: Sequence[Failure] = (),
    asks: Sequence[Ask] | None = None,
) -> Evidence:
    """Evidence as `assemble_evidence` would shape it, from its parts. With no
    `asks`, every ask index the parts name stands for `ANY_ASK`."""

    named = _count_asks_named(results, failures)
    if asks is None:
        asks = (ANY_ASK,) * named
    if named > len(asks):
        # `assemble_evidence` never drops a part; a fake that did would pass by luck.
        raise ValueError(f"parts name {named} asks, but only {len(asks)} given")
    return Evidence(
        sources=tuple(sources),
        results=tuple(results),
        served=tuple(dict.fromkeys(result.ask_index for result in results)),
        failed=tuple(failures),
        ask_evidences=group_by_ask(asks, results=results, failures=failures),
    )


def _count_asks_named(results: Sequence[Result], failures: Sequence[Failure]) -> int:
    indexes = [part.ask_index for part in (*results, *failures)]
    return max(indexes, default=-1) + 1
