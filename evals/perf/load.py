"""Load mode: the question set at a few levels of concurrency.

Closed loop: at step N, N workers each send a turn, wait for its answer, then
send the next. The cost is known up front — each step sends every question
once — which matters with a real LLM behind the DSS. A fixed-rate (open loop)
load would show queueing more honestly, but its cost has no upper limit.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter

import anyio

from evals.perf.questions import Question
from evals.perf.runner import ReadMisses, SendTurn
from evals.perf.turn import TurnTiming


@dataclass(frozen=True)
class StepResult:
    concurrency: int
    # The turns the mock answered. A missed turn is counted, not timed.
    timings: list[TurnTiming]
    missed: int
    # From the first turn sent to the last one answered.
    wall_s: float
    # The turn limit stopped this step before every question was sent.
    truncated: bool = False

    @property
    def turns_per_min(self) -> float:
        """Turns finished per minute of wall time — not the sum of turn
        times, which would count overlapping turns twice. A missed turn
        still took its share of the DSS, so it counts here."""

        return (len(self.timings) + self.missed) / self.wall_s * 60


async def run_load(
    questions: list[Question],
    steps: list[int],
    *,
    turn: SendTurn,
    misses: ReadMisses,
    max_turns: int | None = None,
    warmup: int = 0,
) -> list[StepResult]:
    """`max_turns` caps every turn sent across all steps: each is a real
    model call. A step is skipped once the limit is spent.

    `warmup` turns go first, one at a time, and are not counted: a container
    that has just started pays for cold imports and a first model connection,
    which would otherwise land on the first step — the baseline.
    """

    run_id = uuid.uuid4().hex[:8]
    results = []
    total = warmup + len(questions) * len(steps)
    budget = total if max_turns is None else max_turns
    warm = questions[: min(warmup, budget)]
    for question in warm:
        await turn(question, f"bench-{run_id}-warmup-{question.id}")
    budget -= len(warm)
    for concurrency in steps:
        if budget <= 0:
            break
        allowed = questions[:budget]
        budget -= len(allowed)
        pending = iter(allowed)
        sent: list[tuple[str, TurnTiming]] = []
        prefix = f"bench-{run_id}-n{concurrency}"
        started = perf_counter()
        async with anyio.create_task_group() as group:
            for _ in range(concurrency):
                group.start_soon(_worker, pending, sent, prefix, turn)
        wall_s = perf_counter() - started
        # Keyed by transaction id, so each miss is tied to its own turn even
        # with several turns in flight.
        missed = await misses()
        results.append(
            StepResult(
                concurrency=concurrency,
                timings=[timing for session, timing in sent if session not in missed],
                missed=sum(session in missed for session, _ in sent),
                wall_s=wall_s,
                truncated=len(allowed) < len(questions),
            )
        )
    return results


async def _worker(
    pending: Iterator[Question],
    sent: list[tuple[str, TurnTiming]],
    prefix: str,
    turn: SendTurn,
) -> None:
    """Send the next question each time the last turn is answered.

    The workers share one iterator. That is safe without a lock: tasks only
    switch at an `await`, never inside `next()`.
    """

    for question in pending:
        session_id = f"{prefix}-{question.id}"
        sent.append((session_id, await turn(question, session_id)))
