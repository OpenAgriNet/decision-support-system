"""Tier 1 — the run's shape: warm-up, repeats, misses and the turn limit.

The three things a run talks to — the DSS, the mock's miss list, Langfuse —
are passed in as plain async functions, so these tests hand in fakes.
"""

from __future__ import annotations

from benchmarks.questions import Question
from benchmarks.runner import RunOptions, RunResult, run
from benchmarks.traces import TraceFacts
from benchmarks.turn import TurnIds, TurnTiming

_QUESTIONS = [
    Question(
        id=f"{n}-1", category="weather", text="q", region="r", area="a", point=None
    )
    for n in range(1, 4)
]
_FACTS = TraceFacts(stages={}, calls=[], models={}, flat=False)


class _Fakes:
    """A DSS that answers every turn, a mock with the given misses, and a
    Langfuse holding every trace."""

    def __init__(self, missed: frozenset[str] = frozenset()) -> None:
        self.sent: list[tuple[str, TurnIds]] = []
        self.traced: list[str] = []
        self._missed = missed

    async def turn(self, question: Question, ids: TurnIds) -> TurnTiming:
        self.sent.append((question.id, ids))
        return TurnTiming(status="answered", first_delta_s=1.0, total_s=2.0)

    async def misses(self) -> set[str]:
        """The mock keys a miss by the turn's transaction id."""

        return {ids.transaction_id for q, ids in self.sent if q in self._missed}

    async def trace(self, trace_id: str) -> TraceFacts | None:
        self.traced.append(trace_id)
        return _FACTS


async def _run(fakes: _Fakes, session_id: str = "run-session", **options) -> RunResult:
    return await run(
        _QUESTIONS,
        RunOptions(**options),
        session_id=session_id,
        turn=fakes.turn,
        misses=fakes.misses,
        trace=fakes.trace,
    )


async def test_warm_up_turns_are_sent_but_not_counted():
    """The first turns pay for cold caches and connection set-up, which is
    not what a change to the DSS is being judged on."""

    fakes = _Fakes()

    result = await _run(fakes, warmup=2, repeats=3)

    assert len(fakes.sent) == 2 + 3 * 3
    assert len(result.turns) == 3 * 3


async def test_a_turn_the_mock_could_not_answer_is_marked_a_miss():
    """Its times are real but its answer was not, so the report counts it
    and leaves it out of the figures."""

    result = await _run(_Fakes(missed=frozenset({"2-1"})), warmup=0, repeats=1)

    assert [t.missed for t in result.turns] == [False, True, False]


async def test_each_timed_turn_carries_its_trace():
    result = await _run(_Fakes(), warmup=0, repeats=1)

    assert [t.facts for t in result.turns] == [_FACTS] * 3


async def test_the_turn_limit_caps_every_turn_sent_and_says_the_run_stopped_early():
    """It caps cost, and every turn costs model calls — warm-up included. A
    run cut short must say so, or its figures read as a full run's."""

    fakes = _Fakes()

    result = await _run(fakes, warmup=2, repeats=3, max_turns=4)

    assert len(fakes.sent) == 4
    assert len(result.turns) == 2
    assert result.truncated is True


async def test_every_turn_of_a_run_shares_the_run_session():
    """One Langfuse session per run, so a whole run is found and read in one
    place, warm-up turns included."""

    fakes = _Fakes()

    result = await _run(fakes, session_id="run-session", warmup=1, repeats=2)

    assert {ids.session_id for _, ids in fakes.sent} == {"run-session"}
    assert result.session_id == "run-session"
    assert {t.session_id for t in result.turns} == {"run-session"}


async def test_each_turn_has_its_own_transaction_and_trace_and_is_read_by_trace():
    """With the session shared, the trace id is what tells turns apart in
    Langfuse. The runner picks it, so it can ask for that trace directly."""

    fakes = _Fakes()

    result = await _run(fakes, warmup=0, repeats=2)

    sent = [ids for _, ids in fakes.sent]
    assert len({ids.transaction_id for ids in sent}) == len(sent)
    assert len({ids.trace_id for ids in sent}) == len(sent)
    assert fakes.traced == [t.trace_id for t in result.turns]
    assert [t.trace_id for t in result.turns] == [ids.trace_id for ids in sent]
