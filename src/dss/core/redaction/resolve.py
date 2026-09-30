"""Settle overlapping candidates into the spans that are actually replaced.

Candidates come from the pattern detector and, later, from a model detector
(#136). The longest span wins an overlap; for spans the same length, the one
listed first wins — the rule file's order, then any extra detectors after it.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.redaction.models import Candidate


def resolve(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Non-overlapping candidates, in text order."""

    ranked = sorted(
        range(len(candidates)),
        key=lambda i: (candidates[i].start - candidates[i].end, i),
    )
    chosen: list[Candidate] = []
    for i in ranked:
        candidate = candidates[i]
        if all(
            candidate.end <= kept.start or kept.end <= candidate.start
            for kept in chosen
        ):
            chosen.append(candidate)
    return sorted(chosen, key=lambda c: c.start)
