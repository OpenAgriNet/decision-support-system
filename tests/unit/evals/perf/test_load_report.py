"""Tier 1 — load mode's report: one row per concurrency step."""

from __future__ import annotations

from evals.perf.load import StepResult
from evals.perf.report import load_figures, render_load_text
from evals.perf.turn import TurnTiming


def _step(concurrency: int, totals: list[float], **kwargs) -> StepResult:
    timings = [
        TurnTiming(status="answered", first_delta_s=t / 2, total_s=t) for t in totals
    ]
    return StepResult(concurrency=concurrency, timings=timings, **kwargs)


def test_each_step_reports_throughput_and_its_turn_times():
    steps = [
        _step(1, [2.0, 4.0], missed=0, wall_s=6.0),
        _step(4, [3.0, 5.0], missed=1, wall_s=2.0),
    ]

    rows = load_figures(steps, limits={}, machine={}, commit=None)["steps"]

    assert [r["concurrency"] for r in rows] == [1, 4]
    assert rows[0]["turns_per_min"] == 20.0
    assert rows[1]["total_s"]["max"] == 5.0
    assert rows[1]["missed"] == 1


def test_the_printed_table_shows_each_step_and_the_pinned_limits():
    """The limits sit under the table: a load figure means nothing without
    the CPU and memory the DSS had. Made-up values, handed in."""

    steps = [
        _step(1, [2.0], missed=0, wall_s=2.0),
        _step(4, [3.0], missed=0, wall_s=1.0, truncated=True),
    ]
    report = load_figures(
        steps,
        limits={"cpus": 1.0, "memory_gib": 1.0},
        machine={"processor": "test-cpu"},
        commit="abc1234",
    )

    text = render_load_text(report)

    assert "   1 " in text and "30.0" in text
    assert "   4*" in text and "cut short" in text
    assert "limits: 1 CPU · 1.0 GiB" in text
    assert "test-cpu" in text and "abc1234" in text


def test_each_step_row_says_how_its_turns_ended():
    """Twenty turns failing at once read as a very fast step unless the row
    says they failed."""

    timings = [TurnTiming(status="unavailable", first_delta_s=None, total_s=0.01)]
    step = StepResult(concurrency=1, timings=timings * 20, missed=0, wall_s=0.1)

    text = render_load_text(load_figures([step], limits={}, machine={}, commit=None))

    assert "unavailable 20" in text
