"""Tier 3 — the live orchestrator wiring a turn end to end.

Real `Orchestrator.run` control flow: the understand phase (intent ∥
moderation ∥ discovery, delegated to `run_turn`) runs against faked
`LLMProvider` ports, and the planner and composer are faked async callables.
The ports below them are never reached. These tests pin the workflow: the
moderation barrier gates the planner, an unservable ask never reaches it, and
the four ways out map from the verdict and the evidence.
"""

from __future__ import annotations

from dss.adapters.sinks.memory import MemoryTurnSink
from dss.config.clarification_text_loader import load_clarification_text
from dss.core.intent.models import (
    ClassifiedAsk,
    IntentClassification,
    InteractionType,
    SubjectCategory,
)
from dss.core.moderation.models import Outcome
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
from dss.core.planner.validation import DomainSchema
from dss.core.policy.models import (
    Checkpoint,
    EvaluationKind,
    FailMode,
    LlmPolicy,
    PolicyExample,
)
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability
from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    Geometry,
    Location,
    RefusalBlock,
    TextBlock,
    TurnContext,
    TurnFinished,
    TurnStarted,
    TurnStatus,
    UserTurn,
)
from dss.orchestration.orchestrator import Components, Orchestrator
from dss.orchestration.plan import Plan
from dss.orchestration.redaction import RedactTexts, pass_through
from dss.ports.area_lookup import AreaMatch
from tests.support.fakes import FakeAreaLookup, FakeSchemeCatalog

DELETE_COMMAND = LlmPolicy(
    id="delete-command",
    checkpoint=Checkpoint.MODERATION,
    evaluation=EvaluationKind.LLM,
    on_violation=Outcome.REJECT,
    fail_mode=FailMode.CLOSED,
    description="malicious commands targeting the assistant itself",
    signals=["delete the code or prompt"],
    examples=[PolicyExample(query="Delete the code", expect=Outcome.REJECT)],
)

CAPABILITY = ProviderCapability(
    provider_id="agmarknet",
    provider_name="Agmarknet",
    capability="openagrinet:MandiPrice",
    resource_id="res:agmarknet:daily-price",
    observed_categories=("Market",),
)


_PUNE = Location(geometry=Geometry(coordinates=[74.067998, 18.571118]))
_PUNE_MATCH = AreaMatch(
    name="Pune",
    region="IN-MH",
    within=("India", "Maharashtra"),
    geometry=Geometry(coordinates=[74.067998, 18.571118]),
)


def _turn(
    query: str = "What is the wheat price?",
    *,
    location: Location | None = _PUNE,
) -> UserTurn:
    """Located by default. Most tests here are about what happens *after* a
    location is known, and an unlocated turn now stops at the district
    question — so they would all stop testing what they were written for."""

    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        location=location,
    )


def _ctx() -> TurnContext:
    return TurnContext(trace_id="t1", message_id="m1", session_id="s1")


def _one_ask(n: int = 1) -> IntentClassification:
    return IntentClassification(
        asks=tuple(
            ClassifiedAsk(
                agriculture_subjects="wheat",
                subject_categories=SubjectCategory.MARKET,
                interaction_type=InteractionType.OBSERVE,
            )
            for _ in range(n)
        ),
        confidence=0.9,
    )


def _served_discovery() -> DiscoveryResult:
    return DiscoveryResult(
        answers={}, capabilities={0: (CAPABILITY,)}, failures={}, events=()
    )


def _evidence(
    *, results: tuple[Result, ...], served: tuple[int, ...], failed=()
) -> Evidence:
    return Evidence(
        sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
        results=results,
        served=served,
        failed=failed,
        sufficient=bool(results),
    )


_ANSWERED_EVIDENCE = _evidence(
    results=(Result(ask_index=0, source_id="1", data={"modal": 2275}),),
    served=(0,),
)


class _FakeIntentLLM:
    def __init__(self, result: IntentClassification) -> None:
        self._result = result

    async def structured(self, *, system_prompt, user_query, schema):
        return self._result


class _FakeModerationLLM:
    def __init__(self, *, violated: str | None = None) -> None:
        self._violated = violated

    async def structured(self, *, system_prompt, user_query, schema):
        return schema(violated_policy_id=self._violated)


class _FakeDiscovery:
    def __init__(self, result: DiscoveryResult) -> None:
        self._result = result

    async def __call__(self, intent, turn, now) -> DiscoveryResult:  # noqa: ANN001
        return self._result


class _FakePlan:
    def __init__(self, evidence: Evidence) -> None:
        self._evidence = evidence
        self.calls = 0
        self.verdict_was_set: bool | None = None

    async def __call__(
        self, turn, *, intent, discovery, verdict, visibility
    ) -> Evidence:  # noqa: ANN001
        self.calls += 1
        self.verdict_was_set = verdict.is_set()
        self.turn = turn
        self.visibility = visibility
        return self._evidence


class _FakeCompose:
    """The composer streams — there is only one kind. `chunks` splits the answer
    so a test can see the pieces; `fail_after=n` raises once `n` are out, and
    `closed` records whether the generator's cleanup ran."""

    def __init__(
        self,
        text: str,
        *,
        chunks: tuple[str, ...] | None = None,
        fail_after: int | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else (text,)
        self._fail_after = fail_after
        self.calls = 0
        self.closed = False

    async def __call__(self, evidence, intent, *, turn):  # noqa: ANN001
        self.calls += 1
        self.turn = turn
        try:
            for index, chunk in enumerate(self._chunks):
                if index == self._fail_after:
                    raise RuntimeError("the model stream dropped")
                yield chunk
        finally:
            self.closed = True


class _Telemetry:
    def __init__(self) -> None:
        self.stages: list[str] = []

    def stage(self, name, ctx, outcome) -> None:  # noqa: ANN001
        self.stages.append(name)


def _build(
    *,
    intent: IntentClassification,
    discovery: DiscoveryResult,
    plan: Plan,
    compose: _FakeCompose,
    violated: str | None = None,
    policies=(),
    area_lookup: FakeAreaLookup | None = None,
    redact: RedactTexts = pass_through,
    intent_llm=None,
    moderation_llm=None,
    schemas: dict[str, DomainSchema] | None = None,
) -> tuple[Orchestrator, MemoryTurnSink]:
    turns = MemoryTurnSink()
    orch = Orchestrator(
        schemas=schemas or {},
        intent_llm=intent_llm or _FakeIntentLLM(intent),
        moderation_llm=moderation_llm or _FakeModerationLLM(violated=violated),
        policies=list(policies),
        scheme_catalog=FakeSchemeCatalog(),
        scheme_fuzzy_threshold=None,
        nearest_max_km=50.0,
        components=Components(
            redact=redact,
            discover=_FakeDiscovery(discovery),
            plan=plan,
            compose=compose,
        ),
        turns=turns,
        telemetry=_Telemetry(),
        area_lookup=area_lookup or FakeAreaLookup({"pune": [_PUNE_MATCH]}),
        clarification_text=load_clarification_text(),
    )
    return orch, turns


async def _collect(orch: Orchestrator, turn: UserTurn | None = None):
    return [event async for event in orch.run(turn or _turn(), _ctx())]


async def test_a_served_ask_is_answered_end_to_end() -> None:
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("Wheat is 2,275 Rs [1].")
    orch, turns = _build(
        intent=_one_ask(), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch)

    # the stream: started, one claim, finished — in that order
    assert isinstance(events[0], TurnStarted)
    assert isinstance(events[-1], TurnFinished)
    claims = [e for e in events if isinstance(e, Claim)]
    assert len(claims) == 1
    assert isinstance(claims[0].content, TextBlock)
    assert claims[0].content.text == "Wheat is 2,275 Rs [1]."

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.ANSWERED
    assert finished.sources[0].name == "Agmarknet"
    # the planner ran past the barrier, with the verdict already cleared
    assert plan.calls == 1 and plan.verdict_was_set is True
    assert compose.calls == 1
    # the turn was recorded closed
    assert turns.records["t1"].finished is finished


async def test_moderation_reject_refuses_before_the_planner() -> None:
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("unused")
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=plan,
        compose=compose,
        violated="delete-command",
        policies=(DELETE_COMMAND,),
    )

    events = await _collect(orch)

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REJECTED
    assert any(isinstance(b, RefusalBlock) for b in finished.content)
    # nothing side-effecting ran past the barrier
    assert plan.calls == 0 and compose.calls == 0


async def test_nobody_serving_is_no_match_without_planning() -> None:
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("unused")
    empty = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(intent=_one_ask(), discovery=empty, plan=plan, compose=compose)

    events = await _collect(orch)

    assert events[-1].outcome.status is TurnStatus.NO_MATCH
    # no candidate → the planner and composer are never spent
    assert plan.calls == 0 and compose.calls == 0


async def test_nobody_serving_names_the_place() -> None:
    """Understood, but nobody serves it here. Not the out-of-scope reply."""

    empty = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(
        intent=_one_ask(),
        discovery=empty,
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("unused"),
    )

    events = await _collect(orch, _turn(location=Location(area="Pune")))

    assert [block.text for block in events[-1].content] == [
        "I could not find a source that answers this for Pune yet."
    ]


async def test_an_unlocated_turn_asks_for_a_place() -> None:
    """No coordinates, no area, and the classifier found no place name: there is
    nowhere to search, so ask the farmer instead of discovering, planning and
    composing an answer that could not be local to them. The question asks for
    a place, not a district: a block answers too.
    """

    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("unused")
    orch, _ = _build(
        intent=_one_ask(), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch, _turn(location=None))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert "place" in finished.content[0].text.lower()
    # discovery ran and was discarded — read-only and cheap, and it already
    # starts before moderation clears the turn. What matters is that the planner
    # and composer, which cost real model calls, never ran.
    assert plan.calls == 0 and compose.calls == 0


async def test_an_unlocated_advisory_ask_is_answered() -> None:
    """ "How do I grow potatoes" names nowhere, and the advisory pack indexes
    no location: the turn goes on to the planner and composer instead of
    stopping to ask for a place it does not need."""

    advisory = ProviderCapability(
        provider_id="kvk",
        provider_name="KVK",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:kvk:potato",
        observed_categories=("Crop",),
    )
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("Sow in October [1].")
    orch, _ = _build(
        intent=IntentClassification(
            asks=(
                ClassifiedAsk(
                    agriculture_subjects="potato",
                    subject_categories=SubjectCategory.CROP,
                    interaction_type=InteractionType.ADVISE,
                ),
            ),
            confidence=0.9,
        ),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (advisory,)}, failures={}, events=()
        ),
        plan=plan,
        compose=compose,
        schemas={
            "openagrinet:KnowledgeAdvisory": DomainSchema(
                type="KnowledgeAdvisory", filterable=()
            )
        },
    )

    events = await _collect(orch, _turn("how do I grow potatoes", location=None))

    assert events[-1].outcome.status is TurnStatus.ANSWERED
    assert plan.calls == 1 and compose.calls == 1


async def test_mixed_turn_answers_then_asks_place() -> None:
    """ "How do I grow potato, and will it rain?" with nothing named: the
    potato advice is written, and the answer ends by asking for the place the
    weather ask still needs."""

    advisory = ProviderCapability(
        provider_id="kvk",
        provider_name="KVK",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:kvk:potato",
        observed_categories=("Crop",),
    )
    weather = ProviderCapability(
        provider_id="imd",
        provider_name="IMD",
        capability="openagrinet:WeatherObservation",
        resource_id="res:imd:forecast",
        observed_categories=("Weather",),
    )
    plan = _FakePlan(
        _evidence(
            results=(Result(ask_index=0, source_id="1", data={"sow": "October"}),),
            served=(0,),
        )
    )
    orch, _ = _build(
        intent=IntentClassification(
            asks=(
                ClassifiedAsk(
                    agriculture_subjects="potato",
                    subject_categories=SubjectCategory.CROP,
                    interaction_type=InteractionType.ADVISE,
                ),
                ClassifiedAsk(
                    subject_categories=SubjectCategory.WEATHER,
                    interaction_type=InteractionType.OBSERVE,
                ),
            ),
            confidence=0.9,
        ),
        discovery=DiscoveryResult(
            answers={},
            capabilities={0: (advisory,), 1: (weather,)},
            failures={},
            events=(),
        ),
        plan=plan,
        compose=_FakeCompose("Sow in October [1]."),
        schemas={
            "openagrinet:KnowledgeAdvisory": DomainSchema(
                type="KnowledgeAdvisory", filterable=()
            ),
            "openagrinet:WeatherObservation": DomainSchema(
                type="WeatherObservation", filterable=(), needs_place=True
            ),
        },
    )

    events = await _collect(
        orch, _turn("how do I grow potato, and will it rain?", location=None)
    )

    question = load_clarification_text().needs_place
    deltas = [e.text for e in events if isinstance(e, ClaimDelta)]
    assert deltas[-1] == f"\n\n{question}"
    assert events[-1].outcome.status is TurnStatus.PARTIALLY_ANSWERED


async def test_some_asks_unserved_is_partial() -> None:
    plan = _FakePlan(
        _evidence(
            results=(Result(ask_index=0, source_id="1", data={"modal": 2275}),),
            served=(0,),
        )
    )
    compose = _FakeCompose("Wheat is 2,275 Rs [1]. I could not find the cotton price.")
    orch, _ = _build(
        intent=_one_ask(2), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch)

    assert events[-1].outcome.status is TurnStatus.PARTIALLY_ANSWERED


async def test_one_place_not_found_still_answers_the_other() -> None:
    """ "Weather in Pune and Xyzzy": stopping to ask about Xyzzy would leave
    Pune unanswered. Pune is answered; Xyzzy reaches the composer as a
    failure."""

    def weather_in(place_name: str) -> ClassifiedAsk:
        return ClassifiedAsk(
            subject_categories=SubjectCategory.WEATHER,
            interaction_type=InteractionType.OBSERVE,
            place_name=place_name,
        )

    plan = _FakePlan(
        _evidence(
            results=(Result(ask_index=0, source_id="1", data={"rain": "none"}),),
            served=(0,),
        )
    )
    compose = _FakeCompose("No rain in Pune [1]. I could not find Xyzzy.")
    orch, _ = _build(
        intent=IntentClassification(
            asks=(weather_in("Pune"), weather_in("Xyzzy")), confidence=0.9
        ),
        discovery=_served_discovery(),
        plan=plan,
        compose=compose,
    )

    events = await _collect(orch, _turn("weather in Pune and Xyzzy", location=None))

    assert plan.calls == 1
    assert events[-1].outcome.status is TurnStatus.PARTIALLY_ANSWERED


async def test_an_ambiguous_place_is_asked_after_the_answer_for_the_other() -> None:
    """ "Weather in Pune and Aurangabad": Pune is answered, and the farmer is
    still asked which Aurangabad, so the reply can finish the question."""

    def weather_in(place_name: str) -> ClassifiedAsk:
        return ClassifiedAsk(
            subject_categories=SubjectCategory.WEATHER,
            interaction_type=InteractionType.OBSERVE,
            place_name=place_name,
        )

    def aurangabad(state: str) -> AreaMatch:
        return AreaMatch(
            name="Aurangabad",
            region="IN-XX",
            within=("India", state),
            geometry=Geometry(coordinates=[75.3, 19.9]),
        )

    plan = _FakePlan(
        _evidence(
            results=(Result(ask_index=0, source_id="1", data={"rain": "none"}),),
            served=(0,),
        )
    )
    orch, _ = _build(
        intent=IntentClassification(
            asks=(weather_in("Pune"), weather_in("Aurangabad")), confidence=0.9
        ),
        discovery=_served_discovery(),
        plan=plan,
        compose=_FakeCompose("No rain in Pune [1]."),
        area_lookup=FakeAreaLookup(
            {
                "pune": [_PUNE_MATCH],
                "aurangabad": [aurangabad("Maharashtra"), aurangabad("Bihar")],
            }
        ),
    )

    events = await _collect(
        orch, _turn("weather in Pune and Aurangabad", location=None)
    )

    # One bubble: the question is the last piece of the same streamed answer.
    question = "Which Aurangabad?\n1. Aurangabad, Maharashtra\n2. Aurangabad, Bihar"
    deltas = [e.text for e in events if isinstance(e, ClaimDelta)]
    claims = [e for e in events if isinstance(e, Claim)]
    assert deltas[-1] == f"\n\n{question}"
    assert [c.content.text for c in claims] == [f"No rain in Pune [1].\n\n{question}"]
    assert "".join(deltas) == claims[0].content.text
    finished = events[-1]
    assert finished.outcome.status is TurnStatus.PARTIALLY_ANSWERED
    assert [b.text for b in finished.content] == [claims[0].content.text]


async def test_no_provider_still_asks_which_place() -> None:
    """Nobody serves the Pune ask, and Rampur matches several. The farmer must
    still be asked which Rampur, or a reply can never finish it."""

    def weather_in(place_name: str) -> ClassifiedAsk:
        return ClassifiedAsk(
            subject_categories=SubjectCategory.WEATHER,
            interaction_type=InteractionType.OBSERVE,
            place_name=place_name,
        )

    def rampur(state: str) -> AreaMatch:
        return AreaMatch(
            name="Rampur",
            region="IN-XX",
            within=("India", state),
            geometry=Geometry(coordinates=[79.0, 28.8]),
        )

    plan = _FakePlan(_ANSWERED_EVIDENCE)
    nobody = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(
        intent=IntentClassification(
            asks=(weather_in("Pune"), weather_in("Rampur")), confidence=0.9
        ),
        discovery=nobody,
        plan=plan,
        compose=_FakeCompose("unused"),
        area_lookup=FakeAreaLookup(
            {
                "pune": [_PUNE_MATCH],
                "rampur": [rampur("Uttar Pradesh"), rampur("Himachal Pradesh")],
            }
        ),
    )

    events = await _collect(orch, _turn("weather in Pune and Rampur", location=None))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.content[-1].text == (
        "Which Rampur?\n1. Rampur, Uttar Pradesh\n2. Rampur, Himachal Pradesh"
    )
    assert plan.calls == 0


async def test_all_calls_failing_is_unavailable() -> None:
    plan = _FakePlan(
        _evidence(
            results=(),
            served=(),
            failed=(
                Failure(
                    ask_index=0,
                    capability="openagrinet:MandiPrice",
                    reason="502",
                    retryable=True,
                ),
            ),
        )
    )
    compose = _FakeCompose("The price service could not be reached just now.")
    orch, _ = _build(
        intent=_one_ask(), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch)

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert (
        finished.outcome.cause is not None
        and finished.outcome.cause.value == "provider_unavailable"
    )


async def test_a_place_failure_alone_is_not_an_outage() -> None:
    """Pune's provider had nothing, and Xyzzy was never searched. No service
    was down, so the farmer must not be told to retry one."""

    plan = _FakePlan(
        _evidence(
            results=(),
            served=(),
            failed=(
                Failure(
                    ask_index=1,
                    capability=None,
                    reason="Xyzzy: place not found",
                    retryable=False,
                ),
            ),
        )
    )
    compose = _FakeCompose("No weather for Pune today. I could not find Xyzzy.")
    orch, _ = _build(
        intent=_one_ask(2), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch)

    assert events[-1].outcome.status is TurnStatus.NO_MATCH


async def test_a_streamed_claim_carries_the_sources_it_cites() -> None:
    """A claim is streamed before the terminal frame, so a caller has nothing
    to join a bare `sourceId` to until the turn ends."""

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    events = await _collect(orch)

    claims = [event for event in events if isinstance(event, Claim)]
    assert [source.name for source in claims[0].sources] == ["Agmarknet"]
