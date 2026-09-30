"""One benchmark run: warm-up turns, then every question `repeats` times.

One turn at a time, so each is timed alone. The DSS, the mock's miss list and
Langfuse are passed in as plain async functions, not objects behind an
interface: the run only needs to call them, and a test hands in fakes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from benchmarks.questions import Question
from benchmarks.traces import TraceFacts
from benchmarks.turn import TurnIds, TurnTiming

SendTurn = Callable[[Question, TurnIds], Awaitable[TurnTiming]]
# Transaction ids of the turns the mock could not answer.
ReadMisses = Callable[[], Awaitable[set[str]]]
# A turn's trace, by trace id.
ReadTrace = Callable[[str], Awaitable[TraceFacts | None]]


@dataclass(frozen=True)
class RunOptions:
    warmup: int = 2
    repeats: int = 3
    max_turns: int | None = None


@dataclass(frozen=True)
class TurnResult:
    question_id: str
    category: str
    repeat: int
    session_id: str
    transaction_id: str
    trace_id: str
    timing: TurnTiming
    # The mock had no answer for one of the turn's /select calls, so its
    # times are left out of the figures.
    missed: bool = False
    # Stage times and tokens from the turn's trace. None when Langfuse never
    # ingested it; the client times still count.
    facts: TraceFacts | None = None


@dataclass(frozen=True)
class RunResult:
    turns: list[TurnResult]
    # The Langfuse session every turn of the run was sent under.
    session_id: str | None = None
    # The turn limit stopped the run before every turn was sent.
    truncated: bool = False


async def run(
    questions: list[Question],
    options: RunOptions,
    *,
    session_id: str,
    turn: SendTurn,
    misses: ReadMisses,
    trace: ReadTrace,
) -> RunResult:
    # Every turn the run would send, in order; repeat 0 is a warm-up turn.
    planned = [(q, 0) for q in questions[: options.warmup]] + [
        (q, repeat) for repeat in range(1, options.repeats + 1) for q in questions
    ]
    # The limit counts warm-up turns too: it caps cost, and they cost the same.
    sent = planned[: options.max_turns]

    turns = []
    for question, repeat in sent:
        ids = TurnIds.new(session_id)
        timing = await turn(question, ids)
        if repeat:
            turns.append(
                TurnResult(
                    question_id=question.id,
                    category=question.category,
                    repeat=repeat,
                    session_id=ids.session_id,
                    transaction_id=ids.transaction_id,
                    trace_id=ids.trace_id,
                    timing=timing,
                )
            )

    # Read after the turns rather than between them, so no turn waits on it.
    # The mock keys a miss by transaction id, which the runner set per turn.
    missed = await misses()
    turns = [
        replace(t, missed=t.transaction_id in missed, facts=await trace(t.trace_id))
        for t in turns
    ]
    return RunResult(
        turns=turns, session_id=session_id, truncated=len(sent) < len(planned)
    )
