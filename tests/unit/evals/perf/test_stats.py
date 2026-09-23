"""Tier 1 — summing up a list of timings.

Typical (p50), slow (p95) and worst (max), never a mean: one slow turn drags
a mean up and hides what most turns felt like.
"""

from __future__ import annotations

from evals.perf.stats import summarise


def test_a_hundred_values_give_their_typical_slow_and_worst():
    summary = summarise([float(v) for v in range(1, 101)])

    assert (summary.p50, summary.p95, summary.max, summary.n) == (50, 95, 100, 100)


def test_no_values_give_no_figures_rather_than_a_crash():
    """Every turn can miss, or no turn stream an answer. The report must
    still print, saying there was nothing to measure."""

    summary = summarise([])

    assert (summary.p50, summary.p95, summary.max, summary.n) == (None, None, None, 0)
