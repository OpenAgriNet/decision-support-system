"""Tier 3 — one `select` call consults the client's ranked providers.

The select tool wired into a real Pydantic AI agent, with the network
mocked. These are the tests behind the story's promise that *configuration*
decides which provider answers, not the model: the scripted model always asks
for the same candidate, and what changes between tests is the ranking.

Ordering itself — which candidates survive and in what order — is tier 1
(`tests/unit/core/provider_preferences/`). Here the discovery result arrives
already ranked, exactly as the discovery wrapper hands it over.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
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
from dss.core.provider_preferences.models import AskPriority
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import Geometry, UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent
from dss.ports.invocation import SelectFailedError

WEATHER = "openagrinet:WeatherObservation"
SCHEMAS = {
    "openagrinet:WeatherObservation": DomainSchema(
        type="WeatherObservation", filterable=()
    )
}
CONTEXT_INDEX = {
    "openagrinet:WeatherObservation": (
        "https://schemas.openagrinet.global/schema/WeatherObservation/0.1/context.jsonld"
    )
}


def _capability(provider: str, capability: str = WEATHER) -> ProviderCapability:
    return ProviderCapability(
        provider_id=provider,
        provider_name=provider.title(),
        capability=capability,
        resource_id=f"res:{provider}:forecast",
    )


VISTAAR = _capability("maharashtra-vistaar")
MAUSAMGRAM = _capability("mausamgram")


class _FakeInvocation:
    """Answers per provider: a list of answers, or a `SelectFailedError` to raise.

    A provider missing from `answers` returns nothing, which is the "reached
    them, they had nothing" case the ranking has to walk past.
    """

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self._answers = answers or {}
        self.called: list[str] = []

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        _transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ):
        self.called.append(capability.provider_id)
        reply = self._answers.get(capability.provider_id, ())
        if isinstance(reply, SelectFailedError):
            raise reply
        return reply


def _answer(provider: str, temperature: int) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id=provider,
        provider_name=provider.title(),
        capability=WEATHER,
        resource_id=f"res:{provider}:forecast:today",
        attributes={"parameters": {"temperature": temperature}},
        validity=None,
    )


def _unreachable() -> SelectFailedError:
    return SelectFailedError(WEATHER, 503, FailureClass.TRANSIENT, "upstream is down")


def _intent() -> Intent:
    return Intent(
        asks=(
            Ask(
                agriculture_subjects="rain",
                subject_categories=SubjectCategory.WEATHER,
                interaction_type=InteractionType.OBSERVE,
                place=ResolvedPlace(
                    name="Nashik",
                    within=("Maharashtra",),
                    geometry=Geometry(coordinates=[73.79, 19.99]),
                    source=PlaceSource.NAMED,
                ),
            ),
        ),
        confidence=1.0,
    )


def _ranked(*providers: str) -> dict[int, AskPriority]:
    """The record the discovery wrapper leaves behind when a ranking applied."""

    return {
        0: AskPriority(
            ask_index=0,
            capability="Weather",
            sender_id="kisan-mitra-web",
            source="sender",
            ranking=providers,
            kept=providers,
            dropped=(),
        )
    }


def _deps(
    invocation: _FakeInvocation,
    *,
    candidates: tuple[ProviderCapability, ...],
    priorities: dict[int, AskPriority] | None = None,
) -> PlannerDeps:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return PlannerDeps(
        turn=UserTurn(
            original_query="will it rain in nashik",
            enriched_query="will it rain in nashik",
            transaction_id="txn-1",
            session_id="s-1",
            source_lang="en",
            target_lang="en",
            channel="web",
            sender_id="kisan-mitra-web",
        ),
        intent=_intent(),
        discovery=DiscoveryResult(
            answers={},
            capabilities={0: candidates},
            failures={},
            events=(),
            priorities=priorities or {},
        ),
        schemas=SCHEMAS,
        schema_context_index=CONTEXT_INDEX,
        invocation=invocation,
        verdict=verdict,
    )


def _asks_for(resource_id: str, replies: list[str] | None = None):
    """A model that calls select once for the named candidate, then stops.

    `replies` collects what the tool said back, for the tests about whether
    the model is invited to try another candidate or told the ask is done.
    """

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="select",
                        args={
                            "ask_index": 0,
                            "resource_id": resource_id,
                            "resource_attributes": {},
                        },
                    )
                ]
            )
        if replies is not None:
            replies.extend(
                str(part.content)
                for part in messages[-1].parts
                if isinstance(part, ToolReturnPart)
            )
        return ModelResponse(parts=[TextPart(content="done")])

    return model


def _skill() -> Skill:
    return Skill(
        id="provider-invocation",
        domain="agriculture",
        description="Call providers to answer an ask.",
        guidance="call the tools",
        tool_names=("select",),
    )


async def _run(
    deps: PlannerDeps,
    *,
    asks_for: str = "res:maharashtra-vistaar:forecast",
    replies: list[str] | None = None,
):
    agent = build_planner_agent(skills=(_skill(),))
    with agent.override(model=FunctionModel(_asks_for(asks_for, replies))):
        await agent.run("will it rain", deps=deps)
    return deps


async def test_the_first_choice_answers_when_it_returns_anything() -> None:
    invocation = _FakeInvocation(
        {
            "maharashtra-vistaar": (_answer("maharashtra-vistaar", 29),),
            "mausamgram": (_answer("mausamgram", 31),),
        }
    )
    deps = await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    used = [answer.provider_id for _index, answer in deps.raw_answers]
    assert used == ["maharashtra-vistaar"]


async def test_the_next_choice_answers_when_the_first_has_nothing() -> None:
    """A 200 that committed to nothing is not a failure — it is the first
    choice being spent, and the ranking walking on."""

    invocation = _FakeInvocation({"mausamgram": (_answer("mausamgram", 31),)})
    deps = await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    used = [answer.provider_id for _index, answer in deps.raw_answers]
    assert used == ["mausamgram"]


async def test_every_ranked_provider_is_consulted_even_when_the_first_answers() -> None:
    """The unused answers are what the team compares later to judge whether
    the ranking is ordered the way it should be."""

    invocation = _FakeInvocation(
        {
            "maharashtra-vistaar": (_answer("maharashtra-vistaar", 29),),
            "mausamgram": (_answer("mausamgram", 31),),
        }
    )
    await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    assert sorted(invocation.called) == ["maharashtra-vistaar", "mausamgram"]


async def test_the_unused_answer_never_reaches_the_evidence() -> None:
    """One ask, one provider's answer. Merging two is out of scope, and a
    composer handed both would have no rule for which to believe."""

    invocation = _FakeInvocation(
        {
            "maharashtra-vistaar": (_answer("maharashtra-vistaar", 29),),
            "mausamgram": (_answer("mausamgram", 31),),
        }
    )
    deps = await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    assert len(deps.raw_answers) == 1


async def test_a_first_choice_that_cannot_be_reached_costs_nobody_the_answer() -> None:
    invocation = _FakeInvocation(
        {
            "maharashtra-vistaar": _unreachable(),
            "mausamgram": (_answer("mausamgram", 31),),
        }
    )
    deps = await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    used = [answer.provider_id for _index, answer in deps.raw_answers]
    assert used == ["mausamgram"]
    # Nothing failed as far as the farmer is concerned, so the composer must
    # not be handed an outage to narrate.
    assert deps.failures == []


async def test_nothing_anywhere_on_the_ranking_leaves_the_ask_unanswered() -> None:
    invocation = _FakeInvocation()
    deps = await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    assert deps.raw_answers == []
    # Reached and empty is not an outage either: there is no provider to tell
    # the farmer we could not reach.
    assert deps.failures == []


async def test_an_ask_nobody_could_be_reached_for_is_recorded_as_a_failure() -> None:
    """The one case the farmer should hear "we could not reach them" for."""

    invocation = _FakeInvocation(
        {"maharashtra-vistaar": _unreachable(), "mausamgram": _unreachable()}
    )
    deps = await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    assert deps.raw_answers == []
    assert [failure.ask_index for failure in deps.failures] == [0, 0]
    assert all(failure.retryable for failure in deps.failures)


async def test_an_unranked_ask_calls_only_the_candidate_the_model_named() -> None:
    """No ranking means no fan-out: a deployment that mounts no preferences
    makes exactly the calls it made before this existed."""

    invocation = _FakeInvocation(
        {"maharashtra-vistaar": (_answer("maharashtra-vistaar", 29),)}
    )
    await _run(_deps(invocation, candidates=(VISTAAR, MAUSAMGRAM), priorities={}))

    assert invocation.called == ["maharashtra-vistaar"]


async def test_only_candidates_of_the_chosen_type_are_consulted() -> None:
    """The model authored its fields against one pack's schema. Sending them
    to a provider serving another @type would be a malformed request."""

    other = _capability("mausamgram", capability="openagrinet:MandiPrice")
    invocation = _FakeInvocation(
        {"maharashtra-vistaar": (_answer("maharashtra-vistaar", 29),)}
    )
    await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, other),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        )
    )

    assert invocation.called == ["maharashtra-vistaar"]


async def test_an_unranked_miss_still_invites_the_other_candidates() -> None:
    """Without a ranking, one candidate returning nothing never spent the
    ask: the others were never called, and the model must hear it may still
    try them — the behaviour before any preference existed."""

    replies: list[str] = []
    invocation = _FakeInvocation()  # everyone reached, nobody has anything
    await _run(
        _deps(invocation, candidates=(VISTAAR, MAUSAMGRAM), priorities={}),
        replies=replies,
    )

    assert invocation.called == ["maharashtra-vistaar"]
    assert "Try another candidate" in replies[0]


async def test_a_ranked_provider_of_another_type_keeps_the_ask_open() -> None:
    """A ranked provider serving the topic through another @type was not in
    this call's fan-out — its fields were never authored. The ask is not
    finished until the model has had the chance to pick it."""

    replies: list[str] = []
    other = _capability("mausamgram", capability="openagrinet:MandiPrice")
    invocation = _FakeInvocation()
    await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, other),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        ),
        replies=replies,
    )

    assert "Try another candidate" in replies[0]


async def test_a_spent_ranking_tells_the_model_to_stop() -> None:
    """Every ranked provider was consulted in the one call; another select
    would re-run the same set."""

    replies: list[str] = []
    invocation = _FakeInvocation()
    await _run(
        _deps(
            invocation,
            candidates=(VISTAAR, MAUSAMGRAM),
            priorities=_ranked("maharashtra-vistaar", "mausamgram"),
        ),
        replies=replies,
    )

    assert sorted(invocation.called) == ["maharashtra-vistaar", "mausamgram"]
    assert "Do not call select for this ask again" in replies[0]
