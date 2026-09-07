"""Tier 3 — the orchestrator wiring the whole turn together.

Real ``run_turn`` control flow; every component below it is a fake async callable
(the ports are not even reached). These tests pin the workflow contract, not any
component's behaviour: the fan-out, the moderation barrier, the four ways out, and
the optional reviewer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dss.core.channel.models import ChannelChunk, Status
from dss.core.channel.service import shape as shape_service
from dss.core.composition.models import Answer, Claim, Confidence, Identity
from dss.core.execution.models import Evidence, Result, Source
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome, ReasonCode
from dss.core.planning.models import MissingInput, Plan, Step
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.review.models import ReviewVerdict
from dss.core.shared.models import UserTurn
from dss.core.skills.models import Skills
from dss.core.tool_discovery.models import ToolCandidates
from dss.orchestration.orchestrator import (
    OrchestratorComponents,
    run_turn,
    status_for,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)

IDENTITY = Identity(
    name="Kisan Mitra", persona="helpful", boundaries="agriculture only"
)

INTENT = Intent(
    asks=(
        Ask(
            agriculture_subjects="potato",
            subject_categories=SubjectCategory.MARKET,
            interaction_type=InteractionType.OBSERVE,
        ),
    ),
    confidence=0.9,
)


def _turn(query: str = "potato price?", channel: str = "web") -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel=channel,
    )


class _Recorder:
    """Fake components that record call order, so a test can assert the barrier and
    the fan-out. Each field is overridable per test."""

    def __init__(
        self,
        *,
        decision: ModerationDecision,
        plan: Plan,
        intent: Intent = INTENT,
    ) -> None:
        self.calls: list[str] = []
        self._decision = decision
        self._plan = plan
        self._intent = intent
        self.review_ran = False

    async def classify(self, turn: UserTurn) -> Intent:
        self.calls.append("classify")
        return self._intent

    async def moderate(self, turn: UserTurn) -> ModerationDecision:
        self.calls.append("moderate")
        return self._decision

    async def discover_skills(self, turn: UserTurn, intent: Intent) -> Skills:
        self.calls.append("skills")
        return Skills()

    async def discover_providers(
        self, intent: Intent, turn: UserTurn, now: datetime
    ) -> DiscoveryResult:
        self.calls.append("providers")
        return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())

    async def discover_tools(self, intent: Intent) -> ToolCandidates:
        self.calls.append("tools")
        return ToolCandidates()

    async def plan(self, turn, intent, skills, discovered, tools, verdict) -> Plan:
        self.calls.append("plan")
        await verdict  # the barrier — as the real planner does
        return self._plan

    async def execute(self, plan: Plan) -> Evidence:
        self.calls.append("execute")
        return Evidence(
            sources=(Source(id="1", name="Agmarknet", kind="provider"),),
            results=(Result(step_id=1, source_id="1", data={"modal": 1450}),),
            served=(0,),
            sufficient=True,
        )

    async def compose(self, evidence, plan, identity) -> Answer:
        self.calls.append("compose")
        return Answer(
            claims=(Claim(text="Potato is 1450/quintal.", source_id="1"),),
            sources=evidence.sources,
            confidence=Confidence.HIGH,
        )

    async def review(self, answer, evidence) -> ReviewVerdict:
        self.review_ran = True
        return ReviewVerdict(grounded=True)

    def components(self, *, with_review: bool) -> OrchestratorComponents:
        return OrchestratorComponents(
            classify=self.classify,
            moderate=self.moderate,
            discover_skills=self.discover_skills,
            discover_providers=self.discover_providers,
            discover_tools=self.discover_tools,
            plan=self.plan,
            execute=self.execute,
            compose=self.compose,
            shape=shape_service,
            identity=IDENTITY,
            review=self.review if with_review else None,
        )


async def _drain(agen) -> list[ChannelChunk]:
    return [chunk async for chunk in agen]


_ONE_STEP_PLAN = Plan(
    steps=(Step(id=1, capability="openagrinet:MandiPrice"),), serves=(0,)
)


async def test_proceed_streams_and_honours_the_barrier() -> None:
    rec = _Recorder(
        decision=ModerationDecision(outcome=Outcome.PROCEED),
        plan=_ONE_STEP_PLAN,
    )
    chunks = await _drain(run_turn(_turn(), rec.components(with_review=False), now=NOW))

    # It produced a stream, ending in exactly one final chunk.
    assert chunks
    assert [c.is_final for c in chunks].count(True) == 1
    assert chunks[-1].is_final

    # The barrier: moderation is consumed before anything side-effecting runs.
    assert rec.calls.index("moderate") < rec.calls.index("execute")
    assert rec.calls.index("plan") < rec.calls.index("execute")
    # Fan-out happens after intent, before the plan.
    for step in ("skills", "providers", "tools"):
        assert (
            rec.calls.index("classify")
            < rec.calls.index(step)
            < rec.calls.index("plan")
        )


async def test_reject_sends_one_message_and_never_executes() -> None:
    rec = _Recorder(
        decision=ModerationDecision(
            outcome=Outcome.REJECT, reason_code=ReasonCode.UNSAFE_ILLEGAL
        ),
        plan=_ONE_STEP_PLAN,
    )
    chunks = await _drain(run_turn(_turn(), rec.components(with_review=False), now=NOW))

    assert len(chunks) == 1
    assert chunks[0].is_final
    assert "execute" not in rec.calls  # no outside call on a rejected turn
    assert "compose" not in rec.calls


async def test_proceed_with_empty_plan_is_no_match() -> None:
    rec = _Recorder(
        decision=ModerationDecision(outcome=Outcome.PROCEED),
        plan=Plan(),  # no steps, no missing
    )
    chunks = await _drain(run_turn(_turn(), rec.components(with_review=False), now=NOW))

    assert len(chunks) == 1
    assert "execute" not in rec.calls


async def test_proceed_with_only_missing_is_clarification() -> None:
    rec = _Recorder(
        decision=ModerationDecision(outcome=Outcome.PROCEED),
        plan=Plan(missing=(MissingInput(name="market.state"),)),
    )
    chunks = await _drain(run_turn(_turn(), rec.components(with_review=False), now=NOW))

    assert len(chunks) == 1
    assert "execute" not in rec.calls


async def test_optional_review_runs_only_when_bound() -> None:
    rec = _Recorder(
        decision=ModerationDecision(outcome=Outcome.PROCEED),
        plan=_ONE_STEP_PLAN,
    )
    await _drain(run_turn(_turn(), rec.components(with_review=False), now=NOW))
    assert rec.review_ran is False

    rec2 = _Recorder(
        decision=ModerationDecision(outcome=Outcome.PROCEED),
        plan=_ONE_STEP_PLAN,
    )
    await _drain(run_turn(_turn(), rec2.components(with_review=True), now=NOW))
    assert rec2.review_ran is True


async def test_sms_sends_the_whole_message_as_one_chunk() -> None:
    rec = _Recorder(
        decision=ModerationDecision(outcome=Outcome.PROCEED),
        plan=_ONE_STEP_PLAN,
    )
    chunks = await _drain(
        run_turn(_turn(channel="sms"), rec.components(with_review=False), now=NOW)
    )
    assert len(chunks) == 1
    assert chunks[0].is_final
    assert "[1]" not in chunks[0].text  # sms strips citation markers


def test_status_for_maps_every_outcome() -> None:
    reject = ModerationDecision(
        outcome=Outcome.REJECT, reason_code=ReasonCode.UNSAFE_ILLEGAL
    )
    clarify = ModerationDecision(
        outcome=Outcome.CLARIFY, reason_code=ReasonCode.INTENT_LOW_CONFIDENCE
    )
    no_match = ModerationDecision(
        outcome=Outcome.NO_MATCH, reason_code=ReasonCode.DOMAIN_UNMAPPED
    )
    proceed = ModerationDecision(outcome=Outcome.PROCEED)

    assert status_for(reject, _ONE_STEP_PLAN) is Status.REJECTED
    assert status_for(clarify, _ONE_STEP_PLAN) is Status.NEEDS_CLARIFICATION
    assert status_for(no_match, _ONE_STEP_PLAN) is Status.NO_MATCH
    assert status_for(proceed, _ONE_STEP_PLAN) is Status.ANSWERED
    assert (
        status_for(proceed, Plan(missing=(MissingInput(name="x"),)))
        is Status.NEEDS_CLARIFICATION
    )
    assert status_for(proceed, Plan()) is Status.NO_MATCH
