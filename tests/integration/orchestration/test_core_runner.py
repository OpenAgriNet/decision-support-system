"""The real runner over the real core, with the ports faked.

These are the cases only an assembled system can answer: that moderation gates
the stages after it, that the stages run in the declared order, and that the two
sinks fail in opposite directions.
"""

from __future__ import annotations

import pytest
from tests.support.fakes import FakeTelemetrySink, FakeTurnSink

from dss.adapters.llm.stub import StubLLM
from dss.core.intent.models import ActionType, Intent
from dss.core.shared.models import (
    Cause,
    Claim,
    RefusalBlock,
    TurnFinished,
    TurnStarted,
    TurnStatus,
)
from dss.orchestration.core_runner import CoreRunner

REJECTED_QUERY = "How do I get a gold loan illegally?"


def _llm(confidence: float = 0.9) -> StubLLM:
    return StubLLM(
        {
            Intent: Intent(
                primary_domain="mandi-prices",
                action_type=ActionType.LOOKUP,
                confidence=confidence,
            )
        }
    )


def _runner(llm=None, *, turns=None, telemetry=None) -> CoreRunner:
    return CoreRunner(
        llm=llm or _llm(),
        turns=turns or FakeTurnSink(),
        telemetry=telemetry or FakeTelemetrySink(),
    )


async def _drive(runner, turn, ctx):
    return [event async for event in runner.run(turn, ctx)]


async def test_an_answered_turn_streams_its_claims_then_finishes(a_turn, a_context):
    events = await _drive(_runner(), a_turn(), a_context)

    assert isinstance(events[0], TurnStarted)
    assert all(isinstance(e, Claim) for e in events[1:-1])
    assert len(events) > 2  # at least one claim between the ends
    assert isinstance(events[-1], TurnFinished)
    assert events[-1].outcome.status is TurnStatus.ANSWERED


async def test_a_rejected_turn_emits_no_claims_and_one_refusal(a_turn, a_context):
    events = await _drive(_runner(), a_turn(query=REJECTED_QUERY), a_context)

    assert not [e for e in events if isinstance(e, Claim)]
    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REJECTED
    assert finished.outcome.cause is Cause.UNSAFE_ILLEGAL
    assert [type(b) for b in finished.content] == [RefusalBlock]


async def test_a_rejected_turn_never_asks_the_model_to_classify_intent(
    a_turn, a_context
):
    """Moderation is the gate. If a refused turn still paid for a classification
    call, the early exit would be decorative."""

    llm = _llm()

    await _drive(_runner(llm), a_turn(query=REJECTED_QUERY), a_context)

    assert llm.asked_for(Intent) == 0


async def test_an_unclassifiable_turn_is_a_no_match(a_turn, a_context):
    events = await _drive(_runner(_llm(confidence=0.1)), a_turn(), a_context)

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.NO_MATCH
    assert finished.outcome.cause is Cause.INTENT_LOW_CONFIDENCE
    assert finished.content, "a no-match still has to say something to the farmer"


async def test_the_stages_run_in_the_declared_order(a_turn, a_context):
    telemetry = FakeTelemetrySink()

    await _drive(_runner(telemetry=telemetry), a_turn(), a_context)

    assert [name for name, _ in telemetry.stages] == [
        "moderation",
        "intent",
        "channel",
    ]


async def test_a_broken_telemetry_sink_does_not_fail_the_turn(a_turn, a_context):
    """Telemetry is optional. Losing a span must not cost the farmer an answer."""

    events = await _drive(
        _runner(telemetry=FakeTelemetrySink(fail=True)), a_turn(), a_context
    )

    assert events[-1].outcome.status is TurnStatus.ANSWERED


async def test_no_telemetry_line_carries_the_query(a_turn, a_context):
    telemetry = FakeTelemetrySink()
    query = "my neighbour Ramesh asked about wheat"

    await _drive(_runner(telemetry=telemetry), a_turn(query=query), a_context)

    recorded = " ".join(f"{name} {outcome}" for name, outcome in telemetry.stages)
    assert query not in recorded


async def test_the_turn_record_holds_the_question_and_the_answer(a_turn, a_context):
    turns = FakeTurnSink()

    await _drive(_runner(turns=turns), a_turn(query="Wheat price?"), a_context)

    assert [t.query for t in turns.opened_with] == ["Wheat price?"]
    assert len(turns.closed_with) == 1


async def test_a_broken_turn_sink_fails_the_turn(a_turn, a_context):
    """The turn record is the audit trail. Answering while silently failing to
    record it is not a success worth having."""

    runner = _runner(turns=FakeTurnSink(fail_on="closed"))

    with pytest.raises(RuntimeError):
        await _drive(runner, a_turn(), a_context)
