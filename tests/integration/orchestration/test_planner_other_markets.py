"""Tier 3 — `select` pricing an ask at another market when the one asked
about had no price, wired into a real Pydantic AI Agent."""

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
    FailureClass,
    ProviderCapability,
)
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import Geometry, TextQuery, UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent
from dss.ports.invocation import SelectFailedError
from tests.support.providers import build_capability

MANDI = "openagrinet:MandiPrice"
ONION = "23"
RAHURI_AT = [74.66, 19.64]


def _market(code: str, *, district: str, at: list[float]) -> ProviderCapability:
    return build_capability(
        provider_id="agmarknet",
        provider_name="Agmarknet Vistaar",
        capability=MANDI,
        resource_id=f"resource:mandi-price:market:{code}",
        observed_categories=("Market",),
        advertised={
            "market": {
                "marketCode": code,
                "marketName": f"{code.title()} APMC",
                "district": district,
                "location": {"geo": {"type": "Point", "coordinates": at}},
            },
            "supportedCommodities": [{"code": ONION, "name": "Onion"}],
        },
    )


RAHURI = _market("rahuri", district="338", at=RAHURI_AT)
SANGAMNER = _market("sangamner", district="338", at=[74.21, 19.57])
LASALGAON = _market("lasalgaon", district="339", at=[74.23, 20.15])
PUNE = _market("pune", district="340", at=[73.85, 18.52])


def _price(market: ProviderCapability, modal: int) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id=market.provider_id,
        provider_name=market.provider_name,
        capability=MANDI,
        resource_id=f"{market.resource_id}:2026-11-05",
        attributes={
            "market": {"marketName": market.advertised["market"]["marketName"]},
            "prices": {"modal": modal},
        },
        validity=None,
    )


def _unreachable(market: ProviderCapability) -> SelectFailedError:
    return SelectFailedError(
        capability=market.capability,
        status_code=503,
        failure_class=FailureClass.TRANSIENT,
        detail=None,
    )


class _FakeInvocation:
    """Each market answers with what `replies` holds for it; an exception is
    raised, anything missing is an empty answer."""

    def __init__(
        self, replies: dict[str, list[DiscoveredAnswer] | SelectFailedError]
    ) -> None:
        self.replies = replies
        self.called: list[str] = []

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        _transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> list[DiscoveredAnswer]:
        self.called.append(capability.resource_id)
        reply = self.replies.get(capability.resource_id, [])
        if isinstance(reply, SelectFailedError):
            raise reply
        return reply


def _deps(invocation: _FakeInvocation, *markets: ProviderCapability) -> PlannerDeps:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return PlannerDeps(
        turn=UserTurn(
            input=TextQuery(text="onion price in Rahuri"),
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
                        geometry=Geometry(coordinates=RAHURI_AT),
                        source=PlaceSource.ASSERTED_GEOMETRY,
                    ),
                ),
            ),
            confidence=1.0,
        ),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: markets}, failures={}, events=()
        ),
        schemas={
            MANDI: DomainSchema(
                type="MandiPrice",
                filterable=("supportedCommodities[].code", "market.marketCode"),
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
                            "supportedCommodities": [{"code": ONION}]
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
        result = await agent.run("onion price in Rahuri", deps=deps)
    return next(
        str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )


async def test_an_empty_market_is_answered_from_the_nearest_other_district() -> None:
    invocation = _FakeInvocation(
        {
            LASALGAON.resource_id: [_price(LASALGAON, 1800)],
            PUNE.resource_id: [_price(PUNE, 2100)],
        }
    )
    deps = _deps(invocation, RAHURI, PUNE, LASALGAON)

    told = await _run(deps)

    assert [answer.resource_id for _, answer in deps.raw_answers] == [
        f"{LASALGAON.resource_id}:2026-11-05"
    ]
    assert "Lasalgaon APMC" in told
    assert "Do not call select" in told


async def test_the_search_stops_at_the_first_district_with_a_price() -> None:
    invocation = _FakeInvocation({LASALGAON.resource_id: [_price(LASALGAON, 1800)]})
    deps = _deps(invocation, RAHURI, PUNE, LASALGAON)

    await _run(deps)

    assert invocation.called == [RAHURI.resource_id, LASALGAON.resource_id]


async def test_a_market_in_the_same_district_is_not_asked() -> None:
    """Agmarknet prices the whole district at once, so its neighbour's answer
    is the same empty one."""

    invocation = _FakeInvocation({})
    deps = _deps(invocation, RAHURI, SANGAMNER, LASALGAON)

    await _run(deps)

    assert SANGAMNER.resource_id not in invocation.called


async def test_no_price_anywhere_leaves_the_ask_unanswered() -> None:
    invocation = _FakeInvocation({})
    deps = _deps(invocation, RAHURI, SANGAMNER, LASALGAON, PUNE)

    told = await _run(deps)

    assert deps.raw_answers == []
    assert deps.failures == []
    assert "Do not call select" in told


async def test_an_unreachable_other_market_is_passed_over() -> None:
    """It cost the farmer nothing — the next market had the price — so it is
    not reported as a provider being down."""

    invocation = _FakeInvocation(
        {
            LASALGAON.resource_id: _unreachable(LASALGAON),
            PUNE.resource_id: [_price(PUNE, 2100)],
        }
    )
    deps = _deps(invocation, RAHURI, LASALGAON, PUNE)

    await _run(deps)

    assert [answer.resource_id for _, answer in deps.raw_answers] == [
        f"{PUNE.resource_id}:2026-11-05"
    ]
    assert deps.failures == []


async def test_an_unreachable_market_asked_about_looks_nowhere_else() -> None:
    """Not reaching the provider is not the same as the market having no
    price, and the farmer is told so."""

    invocation = _FakeInvocation(
        {
            RAHURI.resource_id: _unreachable(RAHURI),
            LASALGAON.resource_id: [_price(LASALGAON, 1800)],
        }
    )
    deps = _deps(invocation, RAHURI, LASALGAON)

    await _run(deps)

    assert invocation.called == [RAHURI.resource_id]
    assert len(deps.failures) == 1


async def test_a_market_with_a_price_is_answered_from_itself() -> None:
    invocation = _FakeInvocation(
        {
            RAHURI.resource_id: [_price(RAHURI, 1500)],
            LASALGAON.resource_id: [_price(LASALGAON, 1800)],
        }
    )
    deps = _deps(invocation, RAHURI, LASALGAON)

    await _run(deps)

    assert invocation.called == [RAHURI.resource_id]
