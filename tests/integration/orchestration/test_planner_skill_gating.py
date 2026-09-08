"""Tier 3 — the agent binds tools from the selected skills, nothing else.

An unselected skill's tools must never appear in the model's schema
(ADR-0006). ``AgentInfo.function_tools`` is what the model would actually
see, so that is what these assert on.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Skill, Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent

INVOCATION_SKILL = Skill(
    id="provider-invocation",
    domain="agriculture",
    description="Call providers to answer an ask.",
    guidance="call describe_capability, then select",
    tool_names=("describe_capability", "select"),
)


class _UnusedInvocation:
    """These tests never reach ``select``, but ``invocation`` is required —
    the tool cannot work without one, so the type says so."""

    async def select(self, capability, resource_attributes, transaction_id):
        raise AssertionError("select must not be called in this test")


def _deps() -> PlannerDeps:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
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
        discovery=DiscoveryResult(answers={}, capabilities={}, failures={}, events=()),
        schemas={
            "openagrinet:MandiPrice": DomainSchema(
                type="MandiPrice", filterable=("commodity.code",)
            )
        },
        schema_context_index={},
        invocation=_UnusedInvocation(),
        verdict=verdict,
    )


async def _tool_names_the_model_sees(agent: Agent[PlannerDeps, str]) -> list[str]:
    """Run the agent once and report the tools the model was offered."""

    seen: list[str] = []

    def record(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart(content="done")])

    with agent.override(model=FunctionModel(record)):
        await agent.run("price of paddy", deps=_deps())
    return seen


async def test_the_selected_skills_tools_are_bound() -> None:
    agent = build_planner_agent(skills=(INVOCATION_SKILL,))

    assert sorted(await _tool_names_the_model_sees(agent)) == [
        "describe_capability",
        "select",
    ]


async def test_an_unselected_skills_tools_are_not_bound() -> None:
    """The gate that matters: a skill left out takes its tools with it."""

    partial = INVOCATION_SKILL.model_copy(
        update={"tool_names": ("describe_capability",)}
    )
    agent = build_planner_agent(skills=(partial,))

    assert await _tool_names_the_model_sees(agent) == ["describe_capability"]


async def test_a_skill_naming_an_unknown_tool_fails_at_build_time() -> None:
    """A misconfigured skill must not boot: its guidance would tell the model
    to call a tool that is not there."""

    typo = INVOCATION_SKILL.model_copy(update={"tool_names": ("selcet",)})

    with pytest.raises(KeyError):
        build_planner_agent(skills=(typo,))
