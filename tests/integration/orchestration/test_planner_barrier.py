"""Tier 3 — the moderation barrier: no provider call before the verdict.

A provider call cannot be taken back, so ``select`` must await the moderation
decision before it reaches the network. The verdict here resolves late, so a
tool that skips the await calls the provider while the decision is still
pending — and the test catches it.
"""

from __future__ import annotations

import anyio
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome, ReasonCode
from dss.core.planner.models import Skill, Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.shared.models import UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent

CAPABILITY = ProviderCapability(
    provider_id="agmarknet",
    provider_name="Agmarknet",
    capability="openagrinet:MandiPrice",
    resource_id="res:agmarknet:daily-price",
    observed_categories=("Market",),
)

SCHEMAS = {
    "openagrinet:MandiPrice": DomainSchema(
        type="MandiPrice",
        filterable=("commodity.code",),
    )
}

PROCEED = ModerationDecision(outcome=Outcome.PROCEED)


class _RecordingInvocation:
    """Records whether the verdict had been set when the provider was called."""

    def __init__(self, verdict: Verdict) -> None:
        self._verdict = verdict
        self.calls: list[dict] = []
        self.verdict_was_set: list[bool] = []

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer:
        self.calls.append(resource_attributes)
        self.verdict_was_set.append(self._verdict.is_set())
        return DiscoveredAnswer(
            provider_id=capability.provider_id,
            provider_name=capability.provider_name,
            capability=capability.capability,
            resource_id="res:agmarknet:daily-price:2026-08-25",
            attributes={"prices": {"modal": 2200}},
            validity=None,
        )


def _skill_with(*tool_names: str) -> Skill:
    """A skill carrying just the tools under test — gating itself is tested in
    ``test_planner_skill_gating.py``."""

    return Skill(
        id="provider-invocation",
        domain="agriculture",
        description="Call providers to answer an ask.",
        guidance="call the tools",
        tool_names=tool_names,
    )


def _intent(category: SubjectCategory = SubjectCategory.MARKET) -> Intent:
    """One ask at index 0 — the only index these tests' discovery uses."""

    return Intent(
        asks=(
            Ask(
                agriculture_subjects="paddy",
                subject_categories=category,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=1.0,
    )


def _deps(invocation: _RecordingInvocation, verdict: Verdict) -> PlannerDeps:
    return PlannerDeps(
        turn=UserTurn(
            original_query="price of paddy",
            enriched_query="price of paddy",
            transaction_id="txn-1",
            session_id="s-1",
            source_lang="en",
            target_lang="en",
            channel="web",
        ),
        intent=_intent(),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
        ),
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=invocation,
        verdict=verdict,
    )


def _calls_select_then_answers(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="select",
                    args={
                        "ask_index": 0,
                        "resource_id": "res:agmarknet:daily-price",
                        "resource_attributes": {"commodity": {"code": "PADDY"}},
                    },
                )
            ]
        )
    return ModelResponse(parts=[TextPart(content="Paddy is 2200.")])


@pytest.mark.parametrize(
    "outcome",
    [Outcome.REJECT, Outcome.CLARIFY, Outcome.NO_MATCH],
)
async def test_a_turn_that_does_not_proceed_never_reaches_the_provider(
    outcome: Outcome,
) -> None:
    verdict = Verdict()
    verdict.set(
        ModerationDecision(outcome=outcome, reason_code=ReasonCode.UNSAFE_ILLEGAL)
    )
    invocation = _RecordingInvocation(verdict)
    agent = build_planner_agent(skills=(_skill_with("select"),))

    with agent.override(model=FunctionModel(_calls_select_then_answers)):
        await agent.run("price of paddy", deps=_deps(invocation, verdict))

    assert invocation.calls == []


async def test_select_waits_for_a_late_verdict() -> None:
    verdict = Verdict()
    invocation = _RecordingInvocation(verdict)
    agent = build_planner_agent(skills=(_skill_with("select"),))
    deps = _deps(invocation, verdict)

    async def moderate_late() -> None:
        await anyio.sleep(0.05)
        verdict.set(PROCEED)

    async with anyio.create_task_group() as tg:
        tg.start_soon(moderate_late)
        with agent.override(model=FunctionModel(_calls_select_then_answers)):
            await agent.run("price of paddy", deps=deps)

    assert len(invocation.calls) == 1
    assert invocation.verdict_was_set == [True]


def _calls_select_twice_then_answers(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="select",
                    args={
                        "ask_index": 0,
                        "resource_id": "res:agmarknet:daily-price",
                        "resource_attributes": {"commodity": {"code": code}},
                    },
                )
                for code in ("PADDY", "WHEAT")
            ]
        )
    return ModelResponse(parts=[TextPart(content="Paddy 2200, wheat 2200.")])


async def test_the_barrier_lets_several_select_calls_through() -> None:
    """One turn, two provider calls. Both wait on the same verdict — the
    reason it is an Event and not a coroutine, which can only be awaited
    once."""

    verdict = Verdict()
    invocation = _RecordingInvocation(verdict)
    agent = build_planner_agent(skills=(_skill_with("select"),))
    deps = _deps(invocation, verdict)

    async def moderate_late() -> None:
        await anyio.sleep(0.05)
        verdict.set(PROCEED)

    async with anyio.create_task_group() as tg:
        tg.start_soon(moderate_late)
        with agent.override(model=FunctionModel(_calls_select_twice_then_answers)):
            await agent.run("price of paddy and wheat", deps=deps)

    assert len(invocation.calls) == 2
    assert invocation.verdict_was_set == [True, True]


def _calls_describe_then_answers(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="describe_capability", args={"ask_index": 0})]
        )
    return ModelResponse(parts=[TextPart(content="described")])


async def test_describe_capability_does_not_wait_for_the_verdict() -> None:
    """It renders discovery data already in memory — no network, nothing to
    take back — so it crosses the barrier the way discovery itself does. The
    verdict is never set here: a tool that awaited it would hang."""

    verdict = Verdict()
    invocation = _RecordingInvocation(verdict)
    agent = build_planner_agent(skills=(_skill_with("describe_capability"),))

    deps = _deps(invocation, verdict)
    with anyio.fail_after(1):
        with agent.override(model=FunctionModel(_calls_describe_then_answers)):
            result = await agent.run("what can you tell me?", deps=deps)

    assert result.output == "described"
    assert verdict.is_set() is False
