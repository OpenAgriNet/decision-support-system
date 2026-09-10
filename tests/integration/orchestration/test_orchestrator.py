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
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import Outcome
from dss.core.planner.models import Evidence, Failure, Result, Source, SourceKind
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
from dss.ports.area_lookup import AreaMatch
from tests.support.fakes import FakeAreaLookup

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


def _one_ask(n: int = 1) -> Intent:
    return Intent(
        asks=tuple(
            Ask(
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
    def __init__(self, result: Intent) -> None:
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

    async def __call__(self, turn, *, intent, discovery, verdict) -> Evidence:  # noqa: ANN001
        self.calls += 1
        self.verdict_was_set = verdict.is_set()
        return self._evidence


class _FakeCompose:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def __call__(self, evidence, *, turn) -> str:  # noqa: ANN001
        self.calls += 1
        return self._text


class _Telemetry:
    def __init__(self) -> None:
        self.stages: list[str] = []

    def stage(self, name, ctx, outcome) -> None:  # noqa: ANN001
        self.stages.append(name)


def _build(
    *,
    intent: Intent,
    discovery: DiscoveryResult,
    plan: _FakePlan,
    compose: _FakeCompose,
    violated: str | None = None,
    policies=(),
) -> tuple[Orchestrator, MemoryTurnSink]:
    turns = MemoryTurnSink()
    orch = Orchestrator(
        intent_llm=_FakeIntentLLM(intent),
        moderation_llm=_FakeModerationLLM(violated=violated),
        policies=list(policies),
        components=Components(
            discover=_FakeDiscovery(discovery), plan=plan, compose=compose
        ),
        turns=turns,
        telemetry=_Telemetry(),
        area_lookup=FakeAreaLookup({"pune": [_PUNE_MATCH]}),
        discovery_radius_m=25_000,
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


async def test_an_unlocated_turn_asks_for_a_district() -> None:
    """No coordinates, no area, and the classifier found no place name: there is
    nowhere to search, so ask the farmer instead of discovering, planning and
    composing an answer that could not be local to them.
    """

    plan = _FakePlan(_ANSWERED_EVIDENCE)
    compose = _FakeCompose("unused")
    orch, _ = _build(
        intent=_one_ask(), discovery=_served_discovery(), plan=plan, compose=compose
    )

    events = await _collect(orch, _turn(location=None))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert "district" in finished.content[0].text.lower()
    # discovery ran and was discarded — read-only and cheap, and it already
    # starts before moderation clears the turn. What matters is that the planner
    # and composer, which cost real model calls, never ran.
    assert plan.calls == 0 and compose.calls == 0


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


async def test_all_calls_failing_is_unavailable() -> None:
    plan = _FakePlan(
        _evidence(
            results=(),
            served=(),
            failed=(
                Failure(
                    capability="openagrinet:MandiPrice", reason="502", retryable=True
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
