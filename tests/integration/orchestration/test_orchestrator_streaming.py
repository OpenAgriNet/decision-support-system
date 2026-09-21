"""Tier 3 — the runner releasing the composer's answer as it is written.

There is one composer and it streams; `test_orchestrator.py` covers the
pipeline's control flow, and this file covers what streaming adds to it: a
`ClaimDelta` per piece, the finished `Claim` behind them, and what happens when
a turn is abandoned or the model drops part way through.

The composer is a fake async generator; everything below it is faked exactly as
in `test_orchestrator.py`, which this borrows its fixtures from.
"""

from __future__ import annotations

import pytest

from dss.adapters.llm.stub import StubLLM
from dss.adapters.sinks.memory import MemoryTurnSink
from dss.core.planner.models import Identity
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    TextBlock,
    TurnFinished,
    TurnStarted,
    TurnStatus,
    UserTurn,
)
from dss.core.stream_response.service import build_stream_response
from dss.orchestration.orchestrator import Components, Orchestrator
from tests.integration.orchestration.test_orchestrator import (
    _ANSWERED_EVIDENCE,
    _PUNE_MATCH,
    _ctx,
    _FakeCompose,
    _FakeDiscovery,
    _FakeIntentLLM,
    _FakeModerationLLM,
    _FakePlan,
    _one_ask,
    _served_discovery,
    _Telemetry,
    _turn,
)
from tests.support.fakes import FakeAreaLookup, FakeSchemeCatalog

CHUNKS = ("Wheat is ", "2,2", "75 Rs [1].")
WHOLE = "Wheat is 2,275 Rs [1]."


def _build(
    *,
    compose: _FakeCompose | None = None,
    discovery: DiscoveryResult | None = None,
) -> tuple[Orchestrator, MemoryTurnSink]:
    turns = MemoryTurnSink()
    orch = Orchestrator(
        intent_llm=_FakeIntentLLM(_one_ask()),
        moderation_llm=_FakeModerationLLM(),
        policies=[],
        scheme_catalog=FakeSchemeCatalog(),
        scheme_fuzzy_threshold=None,
        components=Components(
            discover=_FakeDiscovery(discovery or _served_discovery()),
            plan=_FakePlan(_ANSWERED_EVIDENCE),
            compose=compose or _FakeCompose(WHOLE, chunks=CHUNKS),
        ),
        turns=turns,
        telemetry=_Telemetry(),
        area_lookup=FakeAreaLookup({"pune": [_PUNE_MATCH]}),
        discovery_radius_m=25_000,
    )
    return orch, turns


async def _collect(orch: Orchestrator, turn: UserTurn | None = None):
    return [event async for event in orch.run(turn or _turn(), _ctx())]


async def test_the_answer_leaves_in_pieces_before_the_turn_finishes() -> None:
    """The point of the whole feature: words are out while the rest is still
    being written, rather than all arriving with the terminal event."""

    events = await _collect(_build()[0])

    assert [type(e).__name__ for e in events] == [
        "TurnStarted",
        "ClaimDelta",
        "ClaimDelta",
        "ClaimDelta",
        "Claim",
        "TurnFinished",
    ]


async def test_the_pieces_join_back_to_the_answer_that_is_recorded() -> None:
    """What the farmer read as it arrived and what the turn is stored as must be
    the same words — otherwise the stream showed something the record denies."""

    orch, turns = _build()

    events = await _collect(orch)

    streamed = "".join(e.text for e in events if isinstance(e, ClaimDelta))
    claim = next(e for e in events if isinstance(e, Claim))
    finished = events[-1]
    assert streamed == WHOLE
    assert isinstance(claim.content, TextBlock)
    assert claim.content.text == WHOLE
    assert finished.content[0].text == WHOLE
    assert turns.records["t1"].finished is finished


async def test_a_refused_turn_never_reaches_the_composer() -> None:
    """A no-match is a fixed reply, not something the model writes, so there is
    nothing to release early — and the composer must not be spent on one."""

    compose = _FakeCompose(WHOLE, chunks=CHUNKS)
    empty = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(compose=compose, discovery=empty)

    events = await _collect(orch)

    assert events[-1].outcome.status is TurnStatus.NO_MATCH
    assert not any(isinstance(e, ClaimDelta) for e in events)
    assert compose.calls == 0


async def test_a_failure_part_way_through_leaves_the_pieces_already_sent() -> None:
    """No retry and no rollback: the runner cannot un-send them. It propagates,
    and the transport turns it into a failed turn."""

    orch, _ = _build(compose=_FakeCompose(WHOLE, chunks=CHUNKS, fail_after=2))
    seen = []

    with pytest.raises(RuntimeError, match="stream dropped"):
        async for event in orch.run(_turn(), _ctx()):
            seen.append(event)

    assert [e.text for e in seen if isinstance(e, ClaimDelta)] == ["Wheat is ", "2,2"]
    assert not any(isinstance(e, TurnFinished) for e in seen)
    assert isinstance(seen[0], TurnStarted)


async def test_abandoning_the_turn_closes_the_composer() -> None:
    """A client that disconnects mid-answer closes the runner's stream; that has
    to reach the composer, and through it the model. `async for` does not
    propagate a close on its own, so without `aclosing` here the model's
    connection is left to the garbage collector."""

    compose = _FakeCompose(WHOLE, chunks=CHUNKS)
    orch, _ = _build(compose=compose)

    events = orch.run(_turn(), _ctx())
    async for event in events:
        if isinstance(event, ClaimDelta):
            break  # the client hangs up part-way through the answer
    await events.aclose()

    assert compose.closed, "the composer was left open"


async def test_the_real_component_streams_through_the_runner() -> None:
    """Everything above fakes the composer. This one wires the real
    `build_stream_response` over a stub model, so the chain from the component
    to the event stream is exercised rather than assumed."""

    chunks = ("Wheat is ", "2,275 Rs [1].")
    orch, _ = _build()
    orch = Orchestrator(
        intent_llm=_FakeIntentLLM(_one_ask()),
        moderation_llm=_FakeModerationLLM(),
        policies=[],
        scheme_catalog=FakeSchemeCatalog(),
        scheme_fuzzy_threshold=None,
        components=Components(
            discover=_FakeDiscovery(_served_discovery()),
            plan=_FakePlan(_ANSWERED_EVIDENCE),
            compose=build_stream_response(
                identity=Identity(
                    name="Kisan Mitra",
                    persona="A calm, practical farm advisor.",
                    boundaries="Never gives financial advice.",
                ),
                llm=StubLLM(text_chunks=chunks),
            ),
        ),
        turns=MemoryTurnSink(),
        telemetry=_Telemetry(),
        area_lookup=FakeAreaLookup({"pune": [_PUNE_MATCH]}),
        discovery_radius_m=25_000,
    )

    events = await _collect(orch)

    assert [e.text for e in events if isinstance(e, ClaimDelta)] == list(chunks)
    assert events[-1].content[0].text == "".join(chunks)
