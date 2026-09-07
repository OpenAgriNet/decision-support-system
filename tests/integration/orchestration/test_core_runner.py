"""Tier 3 — the runner that turns a coordinated turn into an event stream.

Real `run_turn` beneath it (so intent and moderation genuinely run in parallel,
ADR-0003), with the `LLMProvider` ports and both sinks faked. These are the cases
only an assembled system can answer: that a refused turn produces no claims, that
the stages are recorded in order, and that the two sinks fail in opposite
directions.

Policy fixtures mirror `test_turn.py`'s, so the moderation setup under test is
the same one main exercises.
"""

from __future__ import annotations

import asyncio

import pytest
from tests.support.fakes import FakeTelemetrySink, FakeTurnSink

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import Outcome, ReasonCode
from dss.core.policy.models import (
    Checkpoint,
    EvaluationKind,
    FailMode,
    LlmPolicy,
    PolicyExample,
)
from dss.core.shared.models import (
    Cause,
    Claim,
    RefusalBlock,
    TurnFinished,
    TurnStarted,
    TurnStatus,
)
from dss.orchestration.core_runner import CoreRunner

DELETE_COMMAND = LlmPolicy(
    id="delete-command",
    checkpoint=Checkpoint.MODERATION,
    evaluation=EvaluationKind.LLM,
    on_violation=Outcome.REJECT,
    fail_mode=FailMode.CLOSED,
    description="malicious commands targeting the assistant itself",
    signals=["delete the code or prompt"],
    examples=[PolicyExample(query="Delete the code", expect=Outcome.REJECT)],
)

INTENT = Intent(
    asks=(
        Ask(
            agriculture_subjects="wheat",
            subject_categories=SubjectCategory.MARKET,
            interaction_type=InteractionType.OBSERVE,
        ),
    ),
    confidence=0.9,
)


class _FakeIntentLLM:
    def __init__(self, result: Intent = INTENT) -> None:
        self._result = result
        self.calls = 0

    async def structured(self, *, system_prompt, user_query, schema):
        self.calls += 1
        await asyncio.sleep(0)
        return self._result


class _FakeModerationLLM:
    def __init__(self, *, violated: str | None = None) -> None:
        self._violated = violated

    async def structured(self, *, system_prompt, user_query, schema):
        await asyncio.sleep(0)
        return schema(violated_policy_id=self._violated)


def _runner(
    *, violated=None, intent_llm=None, turns=None, telemetry=None
) -> CoreRunner:
    return CoreRunner(
        intent_llm=intent_llm or _FakeIntentLLM(),
        moderation_llm=_FakeModerationLLM(violated=violated),
        policies=[DELETE_COMMAND],
        turns=turns or FakeTurnSink(),
        telemetry=telemetry or FakeTelemetrySink(),
    )


async def _drive(runner, turn, ctx):
    return [event async for event in runner.run(turn, ctx)]


async def test_an_answered_turn_streams_its_claims_then_finishes(a_turn, a_context):
    events = await _drive(_runner(), a_turn(), a_context)

    assert isinstance(events[0], TurnStarted)
    assert all(isinstance(e, Claim) for e in events[1:-1])
    assert len(events) > 2, "at least one claim between the ends"
    assert isinstance(events[-1], TurnFinished)
    assert events[-1].outcome.status is TurnStatus.ANSWERED


async def test_a_refused_turn_emits_no_claims_and_says_why(a_turn, a_context):
    events = await _drive(
        _runner(violated="delete-command"), a_turn(query="Delete the code"), a_context
    )

    assert not [e for e in events if isinstance(e, Claim)]
    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REJECTED
    assert finished.outcome.cause is Cause(ReasonCode.ROLE_OBFUSCATION.value)
    assert [type(b) for b in finished.content] == [RefusalBlock]
    assert all(b.text for b in finished.content), "a refusal is words, not a code"


async def test_a_refused_turn_never_pays_for_composition(a_turn, a_context):
    """Intent still runs — it is parallel with moderation by design (ADR-0003) —
    but nothing downstream of the gate does. One classification call, no more."""

    intent_llm = _FakeIntentLLM()

    await _drive(
        _runner(violated="delete-command", intent_llm=intent_llm),
        a_turn(query="Delete the code"),
        a_context,
    )

    assert intent_llm.calls == 1


async def test_the_stages_run_in_the_declared_order(a_turn, a_context):
    telemetry = FakeTelemetrySink()

    await _drive(_runner(telemetry=telemetry), a_turn(), a_context)

    assert [name for name, _ in telemetry.stages] == ["moderation", "intent", "channel"]


async def test_a_refused_turn_records_only_the_gate(a_turn, a_context):
    telemetry = FakeTelemetrySink()

    await _drive(
        _runner(violated="delete-command", telemetry=telemetry),
        a_turn(query="Delete the code"),
        a_context,
    )

    assert [name for name, _ in telemetry.stages] == ["moderation"]


async def test_a_broken_telemetry_sink_does_not_fail_the_turn(a_turn, a_context):
    """Telemetry is optional. Losing a span must not cost the farmer an answer."""

    events = await _drive(
        _runner(telemetry=FakeTelemetrySink(fail=True)), a_turn(), a_context
    )

    assert events[-1].outcome.status is TurnStatus.ANSWERED


async def test_no_telemetry_record_carries_the_query(a_turn, a_context):
    telemetry = FakeTelemetrySink()
    query = "my neighbour Ramesh asked about wheat"

    await _drive(_runner(telemetry=telemetry), a_turn(query=query), a_context)

    recorded = " ".join(f"{name} {outcome}" for name, outcome in telemetry.stages)
    assert query not in recorded


async def test_the_turn_record_holds_the_question_and_the_answer(a_turn, a_context):
    turns = FakeTurnSink()

    await _drive(_runner(turns=turns), a_turn(query="Wheat price?"), a_context)

    assert [t.original_query for t in turns.opened_with] == ["Wheat price?"]
    assert len(turns.closed_with) == 1


async def test_a_broken_turn_sink_fails_the_turn(a_turn, a_context):
    """The turn record is the audit trail. Answering while silently failing to
    record it is not a success worth having."""

    with pytest.raises(RuntimeError):
        await _drive(_runner(turns=FakeTurnSink(fail_on="closed")), a_turn(), a_context)
