"""Tier 3 — a provider failure must not fail the turn.

The adapter has already retried and given up by the time the tool sees a
``SelectFailed``. The loop still has to continue: other asks may have other
providers, and one unreachable mandi is not a reason to answer nothing.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.adapters.invocation.client import SelectFailed
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Skill, Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    FailureClass,
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
        type="MandiPrice", filterable=("commodity.code",)
    )
}


class _FailingInvocation:
    """Fails the way the adapter does once its retries are exhausted."""

    def __init__(self) -> None:
        self.calls = 0

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer:
        self.calls += 1
        raise SelectFailed(
            capability.capability,
            429,
            FailureClass.TRANSIENT,
            "too many requests",
        )


def _skill_with(*tool_names: str) -> Skill:
    return Skill(
        id="provider-invocation",
        domain="agriculture",
        description="Call providers to answer an ask.",
        guidance="call the tools",
        tool_names=tool_names,
    )


def _cleared_verdict() -> Verdict:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return verdict


def _deps(invocation: _FailingInvocation) -> PlannerDeps:
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
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
        ),
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=invocation,
        verdict=_cleared_verdict(),
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
    return ModelResponse(parts=[TextPart(content="Could not reach the provider.")])


async def test_a_failed_provider_call_does_not_fail_the_turn() -> None:
    invocation = _FailingInvocation()
    agent = build_planner_agent(skills=(_skill_with("select"),))
    deps = _deps(invocation)

    with agent.override(model=FunctionModel(_calls_select_then_answers)):
        result = await agent.run("price of paddy", deps=deps)

    assert result.output == "Could not reach the provider."
    assert invocation.calls == 1
    assert deps.raw_answers == []


async def test_the_failure_is_recorded_for_the_evidence() -> None:
    """``Evidence.failed`` is what lets the composer say "we could not reach
    Agmarknet" rather than "nobody serves this" — the same empty result, two
    different statements."""

    invocation = _FailingInvocation()
    agent = build_planner_agent(skills=(_skill_with("select"),))
    deps = _deps(invocation)

    with agent.override(model=FunctionModel(_calls_select_then_answers)):
        await agent.run("price of paddy", deps=deps)

    assert len(deps.failures) == 1
    ask_index, failure = deps.failures[0]
    assert ask_index == 0
    assert failure.capability == "openagrinet:MandiPrice"
    assert failure.retryable is True


async def test_the_model_is_told_the_call_failed() -> None:
    """It has to know, or it cannot try another candidate or report the gap."""

    captured: list[str] = []

    def record_tool_result(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        from pydantic_ai.messages import ToolReturnPart

        captured.extend(
            str(part.content)
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        )
        return _calls_select_then_answers(messages, info)

    agent = build_planner_agent(skills=(_skill_with("select"),))

    with agent.override(model=FunctionModel(record_tool_result)):
        await agent.run("price of paddy", deps=_deps(_FailingInvocation()))

    assert any("could not" in text.lower() for text in captured)
