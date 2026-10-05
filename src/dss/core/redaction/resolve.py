"""Settle overlapping spans into the ones that are actually replaced.

Spans come from every configured identifier. The longest span wins an overlap;
for spans the same length, the one listed first wins — identifiers in the order
the config lists them, and each identifier's own order within that.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.redaction.models import PiiSpan


def resolve(spans: Sequence[PiiSpan]) -> list[PiiSpan]:
    """Non-overlapping spans, in text order."""

    ranked = sorted(range(len(spans)), key=lambda i: (spans[i].start - spans[i].end, i))
    chosen: list[PiiSpan] = []
    for i in ranked:
        span = spans[i]
        if all(span.end <= kept.start or kept.end <= span.start for kept in chosen):
            chosen.append(span)
    return sorted(chosen, key=lambda s: s.start)
