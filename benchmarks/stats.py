"""Typical, slow and worst figures for a list of timings."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Summary:
    p50: float | None
    p95: float | None
    max: float | None
    n: int


def summarise(values: list[float]) -> Summary:
    ordered = sorted(values)
    if not ordered:
        return Summary(p50=None, p95=None, max=None, n=0)
    return Summary(
        p50=_nearest_rank(ordered, 0.50),
        p95=_nearest_rank(ordered, 0.95),
        max=ordered[-1],
        n=len(ordered),
    )


def _nearest_rank(ordered: list[float], share: float) -> float:
    """The value at or below which `share` of the values fall.

    Nearest rank, not interpolated: each figure is a turn that really
    happened, not a blend of two.
    """

    return ordered[math.ceil(share * len(ordered)) - 1]
