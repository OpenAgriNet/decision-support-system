"""Tier 3 — plan(), the planner's entry point.

Real agent, real prompt building, mocked ports below. This is the seam the
orchestrator calls: turn in, ``Evidence`` out. The agent's own final text is
discarded — the composer writes the answer from ``Evidence``.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome, ReasonCode
from dss.core.planner.models import Identity, Skill, Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.shared.models import ConversationMessage, UserTurn
from dss.orchestration.plan import build_plan

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

IDENTITY = Identity(
    name="Kisan Mitra",
    persona="A calm, practical farm advisor.",
    boundaries="Never gives financial advice.",
)

SKILL = Skill(
    id="provider-invocation",
    domain="agriculture",
    description="Call providers to answer an ask.",
    guidance="Call describe_capability, then select.",
    tool_names=("select",),
)

PRICE_ASK = Ask(
    agriculture_subjects="paddy",
    subject_categories=SubjectCategory.MARKET,
    interaction_type=InteractionType.OBSERVE,
)


class _FakeInvocation:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
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


def _turn() -> UserTurn:
    return UserTurn(
        original_query="price of paddy",
        enriched_query="price of paddy",
        transaction_id="txn-1",
        session_id="s-1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


def _cleared_verdict() -> Verdict:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return verdict


def _calls_select_then_answers(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    # One ModelRequest holds both the system prompt and the user message, so
    # the first call sees a single message — not two.
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
    return ModelResponse(parts=[TextPart(content="Found the price.")])


async def test_plan_returns_evidence_from_what_the_tools_returned() -> None:
    invocation = _FakeInvocation()
    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=invocation,
        identity=IDENTITY,
        skills=(SKILL,),
        model=FunctionModel(_calls_select_then_answers),
    )

    evidence = await plan(
        _turn(),
        intent=Intent(asks=(PRICE_ASK,), confidence=0.9),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
        ),
        verdict=_cleared_verdict(),
    )

    assert len(invocation.calls) == 1
    assert [source.name for source in evidence.sources] == ["Agmarknet"]
    assert evidence.results[0].data == {"prices": {"modal": 2200}}
    assert evidence.served == (0,)
    assert evidence.sufficient is True


async def test_the_prompt_carries_the_identity_and_the_marked_history() -> None:
    """The model needs the earlier messages to resolve a subject named there
    ("advisory for potato" ... "I am from Pune"), and they must arrive inside
    markers so they read as data. The identity comes from the system prompt."""

    captured: list[str] = []

    def record(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        captured.extend(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, SystemPromptPart | UserPromptPart)
        )
        return ModelResponse(parts=[TextPart(content="done")])

    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=_FakeInvocation(),
        identity=IDENTITY,
        skills=(SKILL,),
        model=FunctionModel(record),
    )
    turn = _turn().model_copy(
        update={
            "enriched_query": "I am from Pune",
            "history": [
                ConversationMessage(role="user", text="Can I get advisory for potato"),
                ConversationMessage(role="assistant", text="Where is your location?"),
            ],
        }
    )

    await plan(
        turn,
        intent=Intent(asks=(PRICE_ASK,), confidence=0.9),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
        ),
        verdict=_cleared_verdict(),
    )

    everything = "\n".join(captured)
    assert "Kisan Mitra" in everything
    assert "Call describe_capability, then select." in everything
    assert "Can I get advisory for potato" in everything
    assert "I am from Pune" in everything
    assert "<BEGIN CONVERSATION>" in everything


DIRECT_ANSWER = DiscoveredAnswer(
    provider_id="krishi-kb",
    provider_name="Krishi Knowledge Base",
    capability="openagrinet:KnowledgeAdvisory",
    resource_id="res:krishi-kb:crop-advisory",
    attributes={"soilType": "sandy loam"},
    validity=None,
)


def _answers_without_calling(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="already known, nothing to call")])


async def test_a_direct_answer_reaches_the_evidence_through_plan() -> None:
    """The seam, not just the pieces. `assemble_evidence` takes
    `direct_answers`, but a tier-1 test of that function passes whether or
    not `plan` actually hands them over — so this drives the real `plan`.

    A Direct answer needs no `select` call, so it never lands in
    `raw_answers`. It used to be shown to the planner and then dropped: the
    planner is told not to answer, and the composer never saw it, so the
    farmer got nothing while the answer sat in the prompt."""

    invocation = _FakeInvocation()
    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": (
                "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
            )
        },
        invocation=invocation,
        identity=IDENTITY,
        skills=(SKILL,),
        model=FunctionModel(_answers_without_calling),
    )

    evidence = await plan(
        _turn(),
        intent=Intent(asks=(PRICE_ASK,), confidence=0.9),
        discovery=DiscoveryResult(
            answers={0: (DIRECT_ANSWER,)}, capabilities={}, failures={}, events=()
        ),
        verdict=_cleared_verdict(),
    )

    assert invocation.calls == []  # no provider call was needed
    assert [source.name for source in evidence.sources] == ["Krishi Knowledge Base"]
    assert evidence.results[0].data == {"soilType": "sandy loam"}
    assert evidence.served == (0,)
    assert evidence.sufficient is True


async def test_the_planner_binds_its_own_model_settings() -> None:
    """ADR-0004: each component binds its own model. The planner was the one
    that did not — it set no temperature, timeout or retries at all, while
    intent and moderation read all three from Settings.

    Retries matter most here: the design raises ``ModelRetry`` in three
    places, and the framework default budget is 1."""

    settings: list[dict] = []

    def record_settings(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings.append(dict(info.model_settings or {}))
        return ModelResponse(parts=[TextPart(content="done")])

    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=_FakeInvocation(),
        identity=IDENTITY,
        skills=(SKILL,),
        model=FunctionModel(record_settings),
        temperature=0.0,
        timeout_seconds=30.0,
        retries=3,
    )

    await plan(
        _turn(),
        intent=Intent(asks=(PRICE_ASK,), confidence=0.9),
        discovery=DiscoveryResult(answers={}, capabilities={}, failures={}, events=()),
        verdict=_cleared_verdict(),
    )

    assert settings[0]["temperature"] == 0.0
    assert settings[0]["timeout"] == 30.0


async def test_a_rejected_turn_yields_empty_insufficient_evidence() -> None:
    """The barrier stops ``select`` inside the tool, so the loop still runs
    and ``plan`` still returns. What it must not return is evidence: no
    provider was called, so there is nothing to answer from."""

    invocation = _FakeInvocation()
    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=invocation,
        identity=IDENTITY,
        skills=(SKILL,),
        model=FunctionModel(_calls_select_then_answers),
    )
    verdict = Verdict()
    verdict.set(
        ModerationDecision(
            outcome=Outcome.REJECT, reason_code=ReasonCode.UNSAFE_ILLEGAL
        )
    )

    evidence = await plan(
        _turn(),
        intent=Intent(asks=(PRICE_ASK,), confidence=0.9),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
        ),
        verdict=verdict,
    )

    assert invocation.calls == []
    assert evidence.results == ()
    assert evidence.sources == ()
    assert evidence.served == ()
    assert evidence.sufficient is False


async def test_a_planner_run_that_fails_still_records_what_it_spent() -> None:
    """The first request is billed, its tool call runs, then the model fails.
    The turn must still carry the first request's cost."""

    from decimal import Decimal

    from pydantic_ai.usage import RequestUsage

    from dss.observability.stages import Stage
    from dss.observability.turn_usage import (
        begin_turn_usage,
        current_turn_usage,
        model_for,
    )

    def select_then_fail(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        if len(messages) == 1:
            response = _calls_select_then_answers(messages, info)
            response.usage = RequestUsage(
                input_tokens=500, output_tokens=40, cost=Decimal("0.003")
            )
            return response
        raise RuntimeError("the model went away")

    begin_turn_usage()
    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/0.1/context.jsonld"
        },
        invocation=_FakeInvocation(),
        identity=IDENTITY,
        skills=(SKILL,),
        model=FunctionModel(select_then_fail),
    )

    with pytest.raises(RuntimeError, match="the model went away"):
        await plan(
            _turn(),
            intent=Intent(asks=(PRICE_ASK,), confidence=0.9),
            discovery=DiscoveryResult(
                answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
            ),
            verdict=_cleared_verdict(),
        )

    usage = current_turn_usage()
    assert usage is not None
    assert usage.cost == pytest.approx(0.003)
    assert model_for(Stage.PLANNER) == "function:select_then_fail:"
