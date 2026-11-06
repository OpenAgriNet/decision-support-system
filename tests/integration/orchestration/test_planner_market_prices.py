"""Tier 3 — `select` keeping the asked market's prices, or the other
markets' when it has none, wired into a real Pydantic AI Agent."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Skill, Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import Geometry, TextQuery, UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent
from tests.support.providers import build_capability

MANDI = "openagrinet:MandiPrice"
RAHURI = build_capability(
    provider_id="agmarknet",
    provider_name="Agmarknet Vistaar",
    capability=MANDI,
    resource_id="resource:mandi-price:market:1634",
    observed_categories=("Market",),
    advertised={
        "market": {"marketCode": "1634", "marketName": "Rahuri APMC"},
        "supportedCommodities": [{"code": "23", "name": "Onion"}],
    },
)


def _price(market: str, day: str) -> DiscoveredAnswer:
    """A row as Agmarknet sends it: the market's name, not its code."""

    return DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="Agmarknet Vistaar",
        capability=MANDI,
        resource_id=f"res:{market.strip()}:{day}",
        attributes={
            "market": {"marketName": market, "district": "Ahilyanagar"},
            "arrivalDate": day,
            "prices": {"modal": 2800},
        },
        validity=None,
    )


class _FakeInvocation:
    def __init__(self, reply: list[DiscoveredAnswer]) -> None:
        self.reply = reply
        self.calls = 0

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        _transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> list[DiscoveredAnswer]:
        self.calls += 1
        return self.reply


def _deps(invocation: _FakeInvocation) -> PlannerDeps:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return PlannerDeps(
        turn=UserTurn(
            input=TextQuery(text="onion price at Rahuri"),
            transaction_id="txn-1",
            session_id="s-1",
            source_lang="en",
            target_lang="en",
            channel="web",
        ),
        intent=Intent(
            asks=(
                Ask(
                    agriculture_subjects="onion",
                    subject_categories=SubjectCategory.MARKET,
                    interaction_type=InteractionType.OBSERVE,
                    place=ResolvedPlace(
                        name="Rahuri",
                        within=("IN-MH",),
                        geometry=Geometry(coordinates=[74.66, 19.64]),
                        source=PlaceSource.ASSERTED_GEOMETRY,
                    ),
                ),
            ),
            confidence=1.0,
        ),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (RAHURI,)}, failures={}, events=()
        ),
        schemas={
            MANDI: DomainSchema(
                type="MandiPrice",
                filterable=("supportedCommodities[].code",),
                needs_place=True,
            )
        },
        schema_context_index={MANDI: "https://example.org/MandiPrice/context.jsonld"},
        invocation=invocation,
        verdict=verdict,
    )


def _selects_rahuri(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="select",
                    args={
                        "ask_index": 0,
                        "resource_id": RAHURI.resource_id,
                        "resource_attributes": {
                            "supportedCommodities": [{"code": "23"}]
                        },
                    },
                )
            ]
        )
    return ModelResponse(parts=[TextPart(content="done")])


async def _run(deps: PlannerDeps) -> str:
    """What `select` told the model."""

    agent = build_planner_agent(
        skills=(
            Skill(
                id="provider-invocation",
                domain="agriculture",
                description="Call providers to answer an ask.",
                guidance="call the tools",
                tool_names=("select",),
            ),
        )
    )
    with agent.override(model=FunctionModel(_selects_rahuri)):
        result = await agent.run("onion price at Rahuri", deps=deps)
    return next(
        str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )


def _answered_with(deps: PlannerDeps) -> list[str]:
    return [answer.resource_id for _, answer in deps.raw_answers]


async def test_the_asked_market_s_latest_price_answers_the_ask() -> None:
    deps = _deps(
        _FakeInvocation(
            [
                _price("APMC Sangamner ", "2026-11-05"),
                _price("APMC Rahuri ", "2026-11-03"),
                _price("APMC Rahuri ", "2026-11-05"),
            ]
        )
    )

    told = await _run(deps)

    assert _answered_with(deps) == ["res:APMC Rahuri:2026-11-05"]
    assert "has no price" not in told


async def test_other_markets_answer_when_the_asked_one_has_no_price() -> None:
    deps = _deps(
        _FakeInvocation(
            [
                _price("APMC Sangamner ", "2026-11-04"),
                _price("APMC Sangamner ", "2026-11-05"),
                _price("APMC Kopargaon ", "2026-11-02"),
            ]
        )
    )

    told = await _run(deps)

    assert _answered_with(deps) == [
        "res:APMC Sangamner:2026-11-05",
        "res:APMC Kopargaon:2026-11-02",
    ]
    assert "Rahuri APMC has no price" in told


async def test_one_select_is_all_it_takes() -> None:
    """The other markets come in the same reply: nobody else is called."""

    invocation = _FakeInvocation([_price("APMC Sangamner ", "2026-11-05")])

    await _run(_deps(invocation))

    assert invocation.calls == 1


async def test_no_price_anywhere_leaves_the_ask_unanswered() -> None:
    deps = _deps(_FakeInvocation([]))

    told = await _run(deps)

    assert deps.raw_answers == []
    assert "no answer" in told
