"""Tier 3 — the live orchestrator wiring a turn end to end.

Real `Orchestrator.run` control flow: the understand phase (intent ∥
moderation ∥ discovery, delegated to `run_turn`) runs against faked
`LLMProvider` ports, and the planner and composer are faked async callables.
The ports below them are never reached. These tests pin the workflow: the
moderation barrier gates the planner, an unservable ask never reaches it, and
the four ways out map from the verdict and the evidence.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import aclosing

import anyio
import pytest

from dss.adapters.sinks.memory import MemoryTurnSink
from dss.config.clarification_text_loader import load_clarification_text
from dss.core.channel.service import NO_MATCH_TEXT, answer_for_session_busy
from dss.core.forms.models import (
    FormBlock,
    FormContext,
    FormDefinition,
    FormEntry,
    FormRecords,
    FormStatus,
    FormSubmission,
)
from dss.core.forms.registry import FormLookup, FormRegistry
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
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.provider_preferences.models import AskPriority
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import (
    Cause,
    Claim,
    ClaimDelta,
    Geometry,
    Location,
    RefusalBlock,
    TextBlock,
    TextQuery,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnInput,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
    UserTurn,
)
from dss.core.shared.session_state import SessionState
from dss.core.translation.service import EdgeTranslation
from dss.orchestration.orchestrator import (
    Components,
    NoPathForInputTypeError,
    Orchestrator,
    TextPath,
    unrouted_input_types,
)
from dss.orchestration.plan import Plan
from dss.orchestration.redaction import RedactTexts, pass_through
from dss.orchestration.session import NoSessionStore, Sessions, SessionStore
from dss.ports.area_lookup import AreaMatch
from tests.support.evidence import ANY_ASK, build_evidence
from tests.support.fakes import (
    FakeAreaLookup,
    FakeLocalizer,
    FakePromptProvider,
    FakeSchemeCatalog,
)
from tests.support.key_value import (
    FailingReadStore,
    HangingReadStore,
    FailingReleaseStore,
    FailingSaveStore,
    FakeKeyValueStore,
    HangingReleaseStore,
    HangingSaveStore,
    HangingStore,
    UnreachableStore,
)
from tests.support.providers import build_capability
from tests.support.turn_events import assert_well_formed

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

CAPABILITY = build_capability(
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
        input=TextQuery(text=query),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        location=location,
    )


def _ctx() -> TurnContext:
    return TurnContext(trace_id="t1", message_id="m1", session_id="s1", sender_id="c1")


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
    *,
    results: tuple[Result, ...],
    failures: tuple[Failure, ...] = (),
    awaiting: dict[int, FormDefinition] | None = None,
    ask_count: int = 1,
) -> Evidence:
    return build_evidence(
        sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
        results=results,
        failures=failures,
        awaiting=awaiting,
        asks=(ANY_ASK,) * ask_count,
    )


_GRIEVANCE_FORM = FormDefinition(
    context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
    fields=(),
)


_ANSWERED_EVIDENCE = _evidence(
    results=(Result(ask_index=0, source_id="1", data={"modal": 2275}),),
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

    async def __call__(self, intent, turn, now) -> DiscoveryResult:
        return self._result


class _FakePlan:
    def __init__(self, evidence: Evidence) -> None:
        self._evidence = evidence
        self.calls = 0
        self.verdict_was_set: bool | None = None

    async def __call__(
        self, turn, *, intent, discovery, verdict, visibility, awaiting=None
    ) -> Evidence:
        self.calls += 1
        self.awaiting = dict(awaiting or {})
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

    async def __call__(self, evidence, *, turn):
        self.calls += 1
        self.evidence = evidence
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

    def stage(self, name, ctx, outcome) -> None:
        self.stages.append(name)


class _FakePath:
    def __init__(self) -> None:
        self.turns: list[UserTurn] = []
        self.started = anyio.Event()

    async def run(self, turn, *, ctx, recorder, session):
        self.turns.append(turn)
        self.started.set()
        yield TurnFinished(
            outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=80)
        )


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
    localize: FakeLocalizer | None = None,
    schemas: dict[str, DomainSchema] | None = None,
    edge_translation: EdgeTranslation | None = None,
    forms: FormLookup | None = None,
    scheme_catalog: FakeSchemeCatalog | None = None,
    sessions: Sessions | None = None,
) -> tuple[Orchestrator, MemoryTurnSink]:
    turns = MemoryTurnSink()
    text_path = TextPath(
        schemas=schemas or {},
        intent_llm=intent_llm or _FakeIntentLLM(intent),
        moderation_llm=moderation_llm or _FakeModerationLLM(violated=violated),
        prompts=FakePromptProvider(),
        policies=list(policies),
        scheme_catalog=scheme_catalog or FakeSchemeCatalog(),
        nearest_max_km=50.0,
        components=Components(
            redact=redact,
            discover=_FakeDiscovery(discovery),
            plan=plan,
            compose=compose,
            localize=localize or FakeLocalizer(),
        ),
        turns=turns,
        telemetry=_Telemetry(),
        area_lookup=area_lookup or FakeAreaLookup({"pune": [_PUNE_MATCH]}),
        clarification_text=load_clarification_text(),
        edge_translation=edge_translation or EdgeTranslation(routes={}),
        forms=forms or FormRegistry(),
    )
    orch = Orchestrator(
        paths={TextQuery: text_path},
        session_busy_answer=answer_for_session_busy(load_clarification_text()),
        sessions=sessions or NoSessionStore(),
    )
    return orch, turns


async def _collect(orch: Orchestrator, turn: UserTurn | None = None):
    events = [event async for event in orch.run(turn or _turn(), _ctx())]
    assert_well_formed(events)
    return events


async def test_a_text_turn_runs_on_its_path() -> None:
    path = _FakePath()
    orch = Orchestrator(
        paths={TextQuery: path},
        session_busy_answer=answer_for_session_busy(load_clarification_text()),
        sessions=NoSessionStore(),
    )

    events = [event async for event in orch.run(_turn(), _ctx())]

    assert isinstance(events[0], TurnStarted)
    assert isinstance(events[1], TurnFinished)
    assert path.turns == [_turn()]


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
    assert turns.records["t1"].finished == finished


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


async def test_an_ask_whose_every_provider_the_client_ruled_out_is_no_match() -> None:
    """The accepted cost of an exclusive ranking (ADR-0019).

    A provider was there and the client's ranking excluded it, so the ask has
    nowhere left to go. The farmer is told we could not find enough
    information — the same reply as "nobody serves this" — and never an
    error, because nothing failed. The planner is not spent on it either.
    """

    ranked_out = DiscoveryResult(
        answers={0: ()},
        capabilities={0: ()},
        failures={},
        events=(),
        priorities={
            0: AskPriority(
                ask_index=0,
                capability="Market",
                sender_id="vistaar-whatsapp",
                source="sender",
                ranking=("state-mandi",),
                kept=(),
                dropped=("agmarknet",),
            )
        },
    )
    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("unused")
    orch, _ = _build(
        intent=_one_ask(), discovery=ranked_out, plan=plan, compose=compose
    )

    events = await _collect(orch)

    assert events[-1].outcome.status is TurnStatus.NO_MATCH
    assert events[-1].outcome.cause is None  # not a failure — a ruled-out ask
    assert plan.calls == 0 and compose.calls == 0


async def test_no_asks_is_no_match_with_a_message() -> None:
    """The classifier found nothing to ask about. Say so, rather than build
    the no-provider reply from zero asks, which is an empty bubble."""

    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("unused")
    empty = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(
        intent=IntentClassification(asks=(), confidence=0.1),
        discovery=empty,
        plan=plan,
        compose=compose,
    )

    events = await _collect(orch)

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.NO_MATCH
    assert [block.text for block in finished.content] == [NO_MATCH_TEXT]
    assert plan.calls == 0 and compose.calls == 0


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

    advisory = build_capability(
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

    advisory = build_capability(
        provider_id="kvk",
        provider_name="KVK",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:kvk:potato",
        observed_categories=("Crop",),
    )
    weather = build_capability(
        provider_id="imd",
        provider_name="IMD",
        capability="openagrinet:WeatherObservation",
        resource_id="res:imd:forecast",
        observed_categories=("Weather",),
    )
    plan = _FakePlan(
        _evidence(
            results=(Result(ask_index=0, source_id="1", data={"sow": "October"}),),
            ask_count=2,
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


async def test_an_ask_awaiting_a_form_ends_the_turn_on_the_form() -> None:
    """The composer's referral comes first, the form last, and the turn waits
    for the farmer: requires_input, no cause."""

    plan = _FakePlan(_evidence(results=(), awaiting={0: _GRIEVANCE_FORM}))
    compose = _FakeCompose("Please fill in the form below.")
    orch, _ = _build(
        intent=_one_ask(), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch)

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is None
    text, form = finished.content
    assert isinstance(text, TextBlock) and text.text == "Please fill in the form below."
    assert isinstance(form, FormBlock) and form.form == _GRIEVANCE_FORM


async def test_some_asks_unserved_is_partial() -> None:
    plan = _FakePlan(
        _evidence(
            results=(Result(ask_index=0, source_id="1", data={"modal": 2275}),),
            ask_count=2,
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
            ask_count=2,
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
            ask_count=2,
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
            failures=(
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
            failures=(
                Failure(
                    ask_index=1,
                    capability=None,
                    reason="Xyzzy: place not found",
                    retryable=False,
                ),
            ),
            ask_count=2,
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


async def test_the_one_source_cited_reaches_the_farmer_as_one() -> None:

    evidence = build_evidence(
        sources=(
            Source(
                id="1", name="Package of Practices", kind=SourceKind.PROVIDER, url=None
            ),
            Source(id="2", name="KAU Mango Guide", kind=SourceKind.PROVIDER, url=None),
        ),
        results=(
            Result(ask_index=0, source_id="1", data={"v": 1}),
            Result(ask_index=0, source_id="2", data={"v": 2}),
        ),
    )
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(evidence),
        compose=_FakeCompose("", chunks=("Plant grafts in June. [", "2]")),
    )

    events = await _collect(orch)

    streamed = "".join(e.text for e in events if isinstance(e, ClaimDelta))
    claim = next(e for e in events if isinstance(e, Claim))
    assert streamed == claim.content.text == "Plant grafts in June. [1]"
    assert [(s.id, s.name) for s in claim.sources] == [("1", "KAU Mango Guide")]
    assert claim.content.source_ids == ("1",)


async def test_an_input_type_without_a_path_names_itself() -> None:
    orch = Orchestrator(
        paths={},
        session_busy_answer=answer_for_session_busy(load_clarification_text()),
        sessions=NoSessionStore(),
    )

    with pytest.raises(NoPathForInputTypeError, match="TextQuery"):
        await _collect(orch)


def test_unrouted_input_types_lists_the_input_type_without_a_path() -> None:
    class _OtpReply(TurnInput):
        code: str

    input_types = [TextQuery, _OtpReply]

    assert unrouted_input_types({TextQuery: _FakePath()}, input_types) == [_OtpReply]


_GRIEVANCE = FormContext(scheme="PM-KISAN", category="Grievance", action="support")


def _form_turn() -> UserTurn:
    return UserTurn(
        input=FormSubmission(
            form_id="f1", context=_GRIEVANCE, submission_id="sub1", values=()
        ),
        session_id="s1",
        transaction_id="t1",
        source_lang=LanguageTag("en"),
        target_lang=LanguageTag("en"),
        channel="web",
    )


def _form_ctx(sender_id: str = "c1", session_id: str = "s1") -> TurnContext:
    return TurnContext(
        trace_id="t1", message_id="m1", session_id=session_id, sender_id=sender_id
    )


def _state_with(form_id: str) -> SessionState:
    entry = FormEntry(
        definition=FormDefinition(context=_GRIEVANCE, fields=()),
        submission=_form_turn().input,
        status=FormStatus.DONE,
    )
    return SessionState(forms={form_id: entry})


def _forms_with(form_id: str) -> FormRecords:
    return _state_with(form_id).forms


class _SessionFormPath:
    def __init__(
        self, *, saves: FormRecords | None = None, hold: anyio.Event | None = None
    ) -> None:
        self.saves = saves
        self.hold = hold
        self.started = anyio.Event()
        self.seen_states: list[SessionState] = []
        self.seen_kept: list[bool] = []
        self.running_count = 0
        self.most_running_at_once = 0

    async def run(self, turn, *, ctx, recorder, session):
        self.seen_states.append(session.state)
        self.seen_kept.append(session.is_kept)
        self.running_count += 1
        self.most_running_at_once = max(self.most_running_at_once, self.running_count)
        self.started.set()
        try:
            if self.saves is not None:
                session.update_forms(self.saves)
            yield ClaimDelta(text="filing")
            if self.hold is not None:
                await self.hold.wait()
            yield TurnFinished(
                outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=80)
            )
        finally:
            self.running_count -= 1


def _session_orchestrator(
    path: _SessionFormPath,
    store: FakeKeyValueStore | None,
    *,
    text_path: _FakePath | None = None,
    acquire_timeout_seconds: float = 30,
    write_deadline_seconds: float = 3,
) -> Orchestrator:
    sessions: Sessions = (
        NoSessionStore()
        if store is None
        else SessionStore(
            store,
            acquire_timeout_seconds=acquire_timeout_seconds,
            write_deadline_seconds=write_deadline_seconds,
        )
    )
    return Orchestrator(
        paths={TextQuery: text_path or _FakePath(), FormSubmission: path},
        session_busy_answer=answer_for_session_busy(load_clarification_text()),
        sessions=sessions,
    )


async def _run_form(
    orch: Orchestrator, *, ctx: TurnContext | None = None
) -> list[TurnEvent]:
    return [event async for event in orch.run(_form_turn(), ctx or _form_ctx())]


def _saved_state(store: FakeKeyValueStore, key: str = "c1:s1") -> SessionState:
    return SessionState.model_validate_json(store.values[key])


async def test_a_form_turn_loads_its_session_and_saves_the_change() -> None:
    store = FakeKeyValueStore()
    store.values["c1:s1"] = _state_with("old").model_dump_json()
    path = _SessionFormPath(saves=_forms_with("new"))

    await _run_form(_session_orchestrator(path, store))

    assert path.seen_states == [_state_with("old")]
    assert path.seen_kept == [True]
    assert _saved_state(store) == _state_with("new")


async def test_an_unchanged_session_is_not_saved() -> None:
    store = FakeKeyValueStore()

    await _run_form(_session_orchestrator(_SessionFormPath(), store))

    assert store.set_calls == []


async def test_a_stream_closed_from_another_task_saves_and_unlocks() -> None:
    store = FakeKeyValueStore()
    path = _SessionFormPath(saves=_forms_with("new"), hold=anyio.Event())
    events = _session_orchestrator(path, store).run(_form_turn(), _form_ctx())

    async for event in events:
        if isinstance(event, ClaimDelta):
            break
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(events.aclose)  # type: ignore[attr-defined]

    assert _saved_state(store) == _state_with("new")
    assert not store.is_locked("c1:s1")


async def test_a_cancelled_consumer_still_saves_and_unlocks() -> None:
    store = FakeKeyValueStore()
    path = _SessionFormPath(saves=_forms_with("new"), hold=anyio.Event())
    orch = _session_orchestrator(path, store)
    filing_seen = anyio.Event()

    # As the transport does on a client disconnect: the stream is closed
    # inside the consumer's cancelled task.
    async def consume() -> None:
        stream = orch.run(_form_turn(), _form_ctx())
        assert isinstance(stream, AsyncGenerator)
        async with aclosing(stream) as events:
            async for event in events:
                if isinstance(event, ClaimDelta):
                    filing_seen.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        await filing_seen.wait()
        tasks.cancel_scope.cancel()

    assert _saved_state(store) == _state_with("new")
    assert not store.is_locked("c1:s1")


async def test_a_failed_save_keeps_the_answer_already_given() -> None:
    path = _SessionFormPath(saves=_forms_with("new"))

    events = await _run_form(_session_orchestrator(path, FailingSaveStore()))

    assert events[-1].outcome.status is TurnStatus.ANSWERED  # type: ignore[union-attr]


async def test_a_failed_release_keeps_the_answer_already_given() -> None:
    events = await _run_form(
        _session_orchestrator(_SessionFormPath(), FailingReleaseStore())
    )

    assert events[-1].outcome.status is TurnStatus.ANSWERED  # type: ignore[union-attr]


@pytest.mark.parametrize("store_kind", [HangingSaveStore, HangingReleaseStore])
async def test_a_hung_store_gives_up_after_the_deadline(
    store_kind: type[HangingStore],
) -> None:
    store = store_kind()
    orch = _session_orchestrator(
        _SessionFormPath(saves=_forms_with("new")),
        store,
        write_deadline_seconds=0.05,
    )
    events: list[TurnEvent] = []
    finished = anyio.Event()
    finished_before_let_go = False

    async def run_turn() -> None:
        events.extend(await _run_form(orch))
        finished.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run_turn)
        with anyio.move_on_after(1):
            await finished.wait()
        finished_before_let_go = finished.is_set()
        store.let_go.set()

    assert finished_before_let_go
    assert events[-1].outcome.status is TurnStatus.ANSWERED  # type: ignore[union-attr]


async def test_two_form_turns_in_one_session_run_one_after_the_other() -> None:
    store = FakeKeyValueStore()
    path = _SessionFormPath(saves=_forms_with("new"), hold=anyio.Event())
    orch = _session_orchestrator(path, store)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_run_form, orch)
        tasks.start_soon(_run_form, orch)
        await path.started.wait()
        await anyio.sleep(0.05)
        assert path.seen_states == [SessionState()]
        assert path.hold is not None
        path.hold.set()

    assert path.most_running_at_once == 1
    assert path.seen_states == [SessionState(), _state_with("new")]


async def test_a_form_turn_past_the_lock_wait_gets_the_busy_answer() -> None:
    store = FakeKeyValueStore()
    path = _SessionFormPath()
    orch = _session_orchestrator(path, store, acquire_timeout_seconds=0.05)

    async with store.lock("c1:s1", lease_seconds=1, acquire_timeout_seconds=1):
        events = await _run_form(orch)

    finished = events[-1]
    assert isinstance(finished, TurnFinished)
    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.TIMEOUT
    assert finished.content == (TextBlock(text=load_clarification_text().session_busy),)
    assert path.seen_states == []
    assert store.set_calls == []


async def test_two_senders_with_one_session_id_keep_two_states() -> None:
    store = FakeKeyValueStore()

    await _run_form(
        _session_orchestrator(_SessionFormPath(saves=_forms_with("c1")), store),
        ctx=_form_ctx(sender_id="c1"),
    )
    second = _SessionFormPath(saves=_forms_with("c2"))
    await _run_form(_session_orchestrator(second, store), ctx=_form_ctx(sender_id="c2"))

    assert second.seen_states == [SessionState()]
    assert set(store.values) == {"c1:s1", "c2:s1"}


async def test_a_text_turn_runs_inside_the_session_and_saves_nothing() -> None:
    store = FakeKeyValueStore()
    orch = _session_orchestrator(_SessionFormPath(), store)

    await _collect(orch)

    assert store.locked_keys == ["c1:s1"]
    assert store.set_calls == []
    assert not store.is_locked("c1:s1")


async def test_a_text_turn_waits_behind_a_form_turn_in_the_same_session() -> None:
    store = FakeKeyValueStore()
    form_path = _SessionFormPath(hold=anyio.Event())
    text_path = _FakePath()
    orch = _session_orchestrator(form_path, store, text_path=text_path)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_run_form, orch)
        await form_path.started.wait()
        tasks.start_soon(_collect, orch)
        await anyio.sleep(0.05)
        assert text_path.turns == []
        assert form_path.hold is not None
        form_path.hold.set()

    assert len(text_path.turns) == 1


async def test_a_text_turn_past_the_lock_wait_answers_busy_without_running() -> None:
    store = FakeKeyValueStore()
    text_path = _FakePath()
    orch = _session_orchestrator(
        _SessionFormPath(), store, text_path=text_path, acquire_timeout_seconds=0.05
    )

    async with store.lock("c1:s1", lease_seconds=1, acquire_timeout_seconds=1):
        events = await _collect(orch)

    finished = events[-1]
    assert isinstance(finished, TurnFinished)
    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.TIMEOUT
    assert text_path.turns == []


async def test_a_form_turn_without_a_store_runs_on_an_empty_session() -> None:
    path = _SessionFormPath(saves=_forms_with("new"))

    events = await _run_form(_session_orchestrator(path, store=None))

    assert isinstance(events[-1], TurnFinished)
    assert path.seen_states == [SessionState()]
    assert path.seen_kept == [False]


async def test_an_unreachable_store_runs_the_turn_on_a_one_turn_session() -> None:
    path = _SessionFormPath(saves=_forms_with("new"))

    events = await _run_form(_session_orchestrator(path, UnreachableStore()))

    assert events[-1].outcome.status is TurnStatus.ANSWERED  # type: ignore[union-attr]
    assert path.seen_states == [SessionState()]
    assert path.seen_kept == [False]


async def test_a_store_that_cannot_be_read_gives_an_empty_session() -> None:
    path = _SessionFormPath()

    await _run_form(_session_orchestrator(path, FailingReadStore()))

    assert path.seen_states == [SessionState()]
    assert path.seen_kept == [False]


async def test_a_session_that_could_not_be_read_is_never_saved_over() -> None:
    store = FailingReadStore()
    store.values["c1:s1"] = _state_with("stored").model_dump_json()
    path = _SessionFormPath(saves=_forms_with("new"))

    await _run_form(_session_orchestrator(path, store))

    assert store.set_calls == []
    assert _saved_state(store) == _state_with("stored")
    assert not store.is_locked("c1:s1")


async def test_a_turn_cancelled_while_its_session_is_read_releases_the_lock() -> None:
    store = HangingReadStore()
    orch = _session_orchestrator(_SessionFormPath(saves=_forms_with("new")), store)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_run_form, orch)
        await store.reading.wait()
        tasks.cancel_scope.cancel()

    assert not store.is_locked("c1:s1")
    assert store.set_calls == []
