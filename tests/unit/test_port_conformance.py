"""Every `TurnRunner` implementation, exercised through the port itself.

`isinstance` against a Protocol checks method *names*, not signatures, and this
repo has no type checker — so nothing else would catch an implementation whose
`run` takes `(turn, context)` while the port says `(turn, ctx)`. Calling through
a port-annotated parameter does.
"""

from __future__ import annotations

import asyncio

from dss.core.shared.models import TurnContext, TurnEvent, TurnFinished, UserTurn
from dss.orchestration.stub_runner import StubRunner
from dss.ports.turn import TurnRunner
from tests.support.fakes import FakeAreaLookup, FakeRunner, FakeSchemeCatalog


def drive(runner: TurnRunner, turn: UserTurn, ctx: TurnContext) -> list[TurnEvent]:
    """Deliberately typed as the port, not as the implementation."""

    async def _collect() -> list[TurnEvent]:
        return [event async for event in runner.run(turn, ctx)]

    return asyncio.run(_collect())


def test_the_stub_runner_satisfies_the_port(a_turn, a_context):
    events = drive(StubRunner(), a_turn(), a_context)

    assert isinstance(events[-1], TurnFinished)


def test_the_fake_runner_satisfies_the_port(a_turn, a_context):
    events = drive(FakeRunner([StubRunner.ANSWER]), a_turn(), a_context)

    assert events == [StubRunner.ANSWER]


async def _no_discovery(intent, turn, *, now):
    """Conformance is about the port's shape, not discovery."""

    from dss.core.provider_discovery.models import DiscoveryResult

    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


async def _unreached_plan(turn, *, intent, discovery, verdict):
    """With `_no_discovery` nobody serves the ask, so the orchestrator answers
    NO_MATCH before the planner runs — this must never be called."""

    raise AssertionError("the planner ran despite an empty discovery")


async def _unreached_compose(evidence, *, turn):
    raise AssertionError("the composer ran despite an empty discovery")


def test_the_orchestrator_satisfies_the_port(a_turn, a_context):
    from dss.adapters.llm.stub import StubLLM
    from dss.adapters.sinks.memory import MemoryTurnSink
    from dss.adapters.sinks.stdout import StdoutTelemetrySink
    from dss.orchestration.orchestrator import Components, Orchestrator

    runner = Orchestrator(
        intent_llm=StubLLM(),
        moderation_llm=StubLLM(),
        policies=[],
        scheme_catalog=FakeSchemeCatalog(),
        scheme_fuzzy_threshold=None,
        components=Components(
            discover=_no_discovery,
            plan=_unreached_plan,
            compose=_unreached_compose,
        ),
        turns=MemoryTurnSink(),
        telemetry=StdoutTelemetrySink(),
        # Empty: this test is about the port contract, not about where the turn
        # searches. Whichever way the turn ends, it ends in a TurnFinished.
        area_lookup=FakeAreaLookup(),
        discovery_radius_m=25_000,
    )

    events = drive(runner, a_turn(), a_context)

    assert isinstance(events[-1], TurnFinished)
