from __future__ import annotations

from collections.abc import Sequence

from dss.core.planner.models import Evidence, Failure, Result, Source


def build_evidence(
    *,
    sources: Sequence[Source] = (),
    results: Sequence[Result] = (),
    failures: Sequence[Failure] = (),
) -> Evidence:
    """Evidence as `assemble_evidence` would shape it, from its parts."""

    return Evidence(
        sources=tuple(sources),
        results=tuple(results),
        served=tuple(dict.fromkeys(result.ask_index for result in results)),
        failed=tuple(failures),
        sufficient=bool(results),
    )
