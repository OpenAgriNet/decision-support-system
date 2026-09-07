"""Tier 3 — the describe_capability tool wired into a real Pydantic AI Agent."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability
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
        type="MandiPrice", filterable=("commodity.code", "market.marketCode")
    )
}


def _deps() -> PlannerDeps:
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
        invocation=None,
    )


def _calls_describe_capability_then_answers(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(
            parts=[ToolCallPart(tool_name="describe_capability", args={"ask_index": 0})]
        )
    return ModelResponse(parts=[TextPart(content="seen it")])


async def test_the_loop_calls_describe_capability_and_continues() -> None:
    agent = build_planner_agent(tool_names=("describe_capability",))

    with agent.override(model=FunctionModel(_calls_describe_capability_then_answers)):
        result = await agent.run("price of paddy", deps=_deps())

    assert result.output == "seen it"


async def test_the_tool_result_carries_the_candidates_filterable_fields() -> None:
    from pydantic_ai.messages import ToolReturnPart

    agent = build_planner_agent(tool_names=("describe_capability",))

    with agent.override(model=FunctionModel(_calls_describe_capability_then_answers)):
        result = await agent.run("price of paddy", deps=_deps())

    tool_returns = [
        part
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(tool_returns) == 1
    assert "commodity.code" in tool_returns[0].content
    assert "market.marketCode" in tool_returns[0].content
