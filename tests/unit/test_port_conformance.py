"""Every `TurnRunner` implementation, exercised through the port itself.

`isinstance` against a Protocol checks method *names*, not signatures, and this
repo has no type checker — so nothing else would catch an implementation whose
`run` takes `(turn, context)` while the port says `(turn, ctx)`. Calling through
a port-annotated parameter does.
"""

from __future__ import annotations

import asyncio

from tests.support.fakes import FakeRunner

from dss.core.shared.models import TurnContext, TurnEvent, TurnFinished, UserTurn
from dss.orchestration.stub_runner import StubRunner
from dss.ports.turn import TurnRunner


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


def test_the_core_runner_satisfies_the_port(a_turn, a_context):
    from dss.adapters.llm.stub import StubLLM
    from dss.adapters.sinks.memory import MemoryTurnSink
    from dss.adapters.sinks.stdout import StdoutTelemetrySink
    from dss.core.intent.models import ActionType, Intent
    from dss.orchestration.core_runner import CoreRunner

    runner = CoreRunner(
        llm=StubLLM(
            {
                Intent: Intent(
                    primary_domain="mandi-prices",
                    action_type=ActionType.LOOKUP,
                    confidence=0.9,
                )
            }
        ),
        turns=MemoryTurnSink(),
        telemetry=StdoutTelemetrySink(),
    )

    events = drive(runner, a_turn(), a_context)

    assert isinstance(events[-1], TurnFinished)
