"""Tier 3 — the orchestrator wiring the turn together.

Real ``run_turn`` control flow; every component below it is a fake async callable
(the ports are not even reached). These tests pin the workflow contract: intent and
moderation run together, the moderation barrier gates discovery and the planner, and
the four ways out are decided from the verdict and the plan.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome, ReasonCode
from dss.core.planning.models import MissingInput, Plan, Step
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability
from dss.core.shared.models import UserTurn
from dss.orchestration.orchestrator import (
    OrchestratorComponents,
    outcome_for,
    run_turn,
)

NOW = datetime(2026, 9, 8, tzinfo=UTC)

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


def _turn(query: str = "potato price?") -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


def _one_capability(index: int) -> tuple[ProviderCapability, ...]:
    return (
        ProviderCapability(
            provider_id="agmarknet",
            provider_name="Agmarknet",
            capability="openagrinet:MandiPrice",
            resource_id=f"res:{index}",
        ),
    )


class _Recorder:
    """Fake components that record call order and let each test pick the verdict and
    what discovery returns."""

    def __init__(
        self,
        *,
        decision: ModerationDecision,
        served: bool = True,
        intent: Intent = INTENT,
    ) -> None:
        self.calls: list[str] = []
        self._decision = decision
        self._served = served
        self._intent = intent

    async def classify(self, turn: UserTurn) -> Intent:
        self.calls.append("classify")
        return self._intent

    async def moderate(self, turn: UserTurn) -> ModerationDecision:
        self.calls.append("moderate")
        return self._decision

    async def discover_providers(
        self, intent: Intent, turn: UserTurn, now: datetime
    ) -> DiscoveryResult:
        self.calls.append("discover")
        capabilities = (
            {i: _one_capability(i) for i in range(len(intent.asks))}
            if self._served
            else {}
        )
        return DiscoveryResult(
            answers={}, capabilities=capabilities, failures={}, events=()
        )

    async def plan(self, turn, intent, discovered, decision) -> Plan:
        self.calls.append("plan")
        steps = tuple(
            Step(id=i + 1, capability=discovered.capabilities[i][0].capability)
            for i in discovered.capabilities
        )
        return Plan(steps=steps, serves=tuple(discovered.capabilities))

    def components(self) -> OrchestratorComponents:
        return OrchestratorComponents(
            classify=self.classify,
            moderate=self.moderate,
            discover_providers=self.discover_providers,
            plan=self.plan,
        )


async def test_proceed_plans_and_honours_the_barrier() -> None:
    rec = _Recorder(decision=ModerationDecision(outcome=Outcome.PROCEED))
    result = await run_turn(_turn(), rec.components(), now=NOW)

    assert result.outcome is Outcome.PROCEED
    assert result.plan is not None and result.plan.steps
    assert result.intent == INTENT
    # Intent and moderation both run before the barrier; discovery and the planner
    # only after moderation is consumed.
    assert {"classify", "moderate"} <= set(rec.calls)
    assert rec.calls.index("moderate") < rec.calls.index("discover")
    assert rec.calls.index("discover") < rec.calls.index("plan")


async def test_reject_blanks_intent_and_never_discovers_or_plans() -> None:
    rec = _Recorder(
        decision=ModerationDecision(
            outcome=Outcome.REJECT, reason_code=ReasonCode.UNSAFE_ILLEGAL
        )
    )
    result = await run_turn(_turn(), rec.components(), now=NOW)

    assert result.outcome is Outcome.REJECT
    assert result.plan is None
    assert result.intent == Intent()  # blanked (ADR-0003)
    assert "discover" not in rec.calls  # no outside work past the barrier
    assert "plan" not in rec.calls


async def test_proceed_but_nobody_serves_is_no_match() -> None:
    rec = _Recorder(decision=ModerationDecision(outcome=Outcome.PROCEED), served=False)
    result = await run_turn(_turn(), rec.components(), now=NOW)

    assert result.outcome is Outcome.NO_MATCH
    assert result.plan is not None and not result.plan.steps


def test_outcome_for_maps_every_case() -> None:
    reject = ModerationDecision(
        outcome=Outcome.REJECT, reason_code=ReasonCode.UNSAFE_ILLEGAL
    )
    proceed = ModerationDecision(outcome=Outcome.PROCEED)
    one_step = Plan(steps=(Step(id=1, capability="c"),))

    assert outcome_for(reject, one_step) is Outcome.REJECT
    assert outcome_for(proceed, one_step) is Outcome.PROCEED
    assert (
        outcome_for(proceed, Plan(missing=(MissingInput(name="x"),))) is Outcome.CLARIFY
    )
    assert outcome_for(proceed, Plan()) is Outcome.NO_MATCH
