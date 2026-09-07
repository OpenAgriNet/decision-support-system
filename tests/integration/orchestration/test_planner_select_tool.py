"""Tier 3 — the select tool wired into a real Pydantic AI Agent.

FunctionModel scripts a tool-call sequence; the loop runs it against real
tool-calling machinery with the network (CapabilityInvocation) mocked.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
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
        filterable=("commodity.code", "market.marketCode", "market.state"),
    )
}


class _FakeInvocation:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        _transaction_id: str,
    ) -> DiscoveredAnswer:
        self.calls.append(resource_attributes)
        return DiscoveredAnswer(
            provider_id=capability.provider_id,
            provider_name=capability.provider_name,
            capability=capability.capability,
            resource_id="res:agmarknet:daily-price:2026-08-25",
            attributes={"prices": {"modal": 2200}},
            validity=None,
        )


def _deps(invocation: _FakeInvocation) -> PlannerDeps:
    from dss.core.provider_discovery.models import DiscoveryResult

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
        schema_context_index={"openagrinet:MandiPrice": ("MandiPrice", "0.1")},
        schema_base_url="https://schemas.openagrinet.global/schema",
        invocation=invocation,
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


async def test_the_loop_calls_select_and_stops() -> None:
    invocation = _FakeInvocation()
    agent = build_planner_agent(tool_names=("select",))

    with agent.override(model=FunctionModel(_calls_select_then_answers)):
        result = await agent.run("price of paddy", deps=_deps(invocation))

    assert result.output == "Paddy is 2200."
    assert len(invocation.calls) == 1
    assert invocation.calls[0]["commodity"] == {"code": "PADDY"}
    assert invocation.calls[0]["@type"] == "openagrinet:MandiPrice"


async def test_the_raw_answer_is_accumulated_on_deps() -> None:
    invocation = _FakeInvocation()
    agent = build_planner_agent(tool_names=("select",))
    deps = _deps(invocation)

    with agent.override(model=FunctionModel(_calls_select_then_answers)):
        await agent.run("price of paddy", deps=deps)

    assert len(deps.raw_answers) == 1
    ask_index, answer = deps.raw_answers[0]
    assert ask_index == 0
    assert answer.attributes == {"prices": {"modal": 2200}}


def _last_part_is_a_retry_prompt(messages: list[ModelMessage]) -> bool:
    last_parts = messages[-1].parts
    return bool(last_parts) and isinstance(last_parts[-1], RetryPromptPart)


def _calls_select_with_invented_field(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    if _last_part_is_a_retry_prompt(messages):
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
    if len(messages) == 1:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="select",
                    args={
                        "ask_index": 0,
                        "resource_id": "res:agmarknet:daily-price",
                        "resource_attributes": {"cropVariety": "Basmati"},
                    },
                )
            ]
        )
    return ModelResponse(parts=[TextPart(content="corrected")])


async def test_an_invalid_argument_gets_a_retry() -> None:
    invocation = _FakeInvocation()
    agent = build_planner_agent(tool_names=("select",))

    with agent.override(model=FunctionModel(_calls_select_with_invented_field)):
        result = await agent.run("price of paddy", deps=_deps(invocation))

    assert result.output == "corrected"
    assert len(invocation.calls) == 1
    assert invocation.calls[0]["commodity"] == {"code": "PADDY"}
