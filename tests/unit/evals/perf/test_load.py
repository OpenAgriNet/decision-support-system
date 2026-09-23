"""Tier 1 — load mode: the question set at a few levels of concurrency.

Closed loop: N workers each send a turn, wait for it, then send the next.
The DSS and the mock's miss list are passed in as fakes.
"""

from __future__ import annotations

import anyio

from evals.perf.load import run_load
from evals.perf.questions import Question
from evals.perf.turn import TurnTiming

_QUESTIONS = [
    Question(
        id=f"{n}-1", category="weather", text="q", region="r", area="a", point=None
    )
    for n in range(1, 13)
]


class _Dss:
    """Answers each turn after a short pause, counting how many overlap."""

    def __init__(self, missing: str | None = None) -> None:
        self.in_flight = 0
        self.peak = 0
        self.sent: list[str] = []
        self._missing = missing

    async def turn(self, question: Question, session_id: str) -> TurnTiming:
        self.sent.append(session_id)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await anyio.sleep(0.01)
        self.in_flight -= 1
        return TurnTiming(status="answered", first_delta_s=0.005, total_s=0.01)

    async def misses(self) -> set[str]:
        """The mock's miss list: the transaction id of the question given."""

        return {s for s in self.sent if self._missing and s.endswith(self._missing)}


async def test_no_more_turns_than_the_step_allows_are_ever_in_flight():
    dss = _Dss()

    await run_load(_QUESTIONS, steps=[4], turn=dss.turn, misses=dss.misses)

    assert dss.peak == 4


async def test_each_step_reports_its_turns_per_minute_over_its_wall_time():
    """The throughput figure: how many turns one DSS finished per minute at
    that concurrency. Wall time, not the sum of turn times, which would count
    overlapping turns twice."""

    dss = _Dss()

    (step,) = await run_load(_QUESTIONS, steps=[4], turn=dss.turn, misses=dss.misses)

    # 12 turns, 4 at a time, 10 ms each: about 3 rounds of 10 ms.
    assert 0.025 < step.wall_s < 0.1
    assert step.turns_per_min == len(_QUESTIONS) / step.wall_s * 60


async def test_a_miss_under_load_is_tied_to_its_own_turn_and_left_out():
    """With four turns in flight, a before-and-after miss count could not say
    which turn missed. The transaction id can."""

    dss = _Dss(missing="-7-1")

    (step,) = await run_load(_QUESTIONS, steps=[4], turn=dss.turn, misses=dss.misses)

    assert step.missed == 1
    assert len(step.timings) == len(_QUESTIONS) - 1


async def test_the_turn_limit_caps_load_across_steps_and_marks_the_cut_step():
    """Each turn is a real model call. A step cut short has fewer turns than
    the others, so its figures must say so."""

    dss = _Dss()

    first, second = await run_load(
        _QUESTIONS, steps=[1, 4], turn=dss.turn, misses=dss.misses, max_turns=15
    )

    assert len(dss.sent) == 15
    assert (len(first.timings), first.truncated) == (12, False)
    assert (len(second.timings), second.truncated) == (3, True)
