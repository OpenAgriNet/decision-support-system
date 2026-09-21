"""Tier 3 — the orchestrator wired to stream the composer's answer.

Same runner, same pipeline, one different binding: `Components.compose_stream`
is set, so the answer leaves as it is written instead of in one piece at the
end. These pin what that changes and — more importantly — what it does not.

The composer is a fake async generator; everything below it is faked exactly as
in `test_orchestrator.py`, which this borrows its fixtures from.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from dss.adapters.sinks.memory import MemoryTurnSink
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


class _FakeComposeStream:
    """Yields fixed pieces. `fail_after=n` raises once `n` are out."""

    def __init__(
        self, chunks: tuple[str, ...] = CHUNKS, *, fail_after: int | None = None
    ) -> None:
        self._chunks = chunks
        self._fail_after = fail_after
        self.calls = 0

    async def __call__(self, evidence, *, turn) -> AsyncIterator[str]:  # noqa: ANN001
        self.calls += 1
        for index, chunk in enumerate(self._chunks):
            if index == self._fail_after:
                raise RuntimeError("the model stream dropped")
            yield chunk


def _build(
    *,
    compose_stream: _FakeComposeStream | None,
    discovery: DiscoveryResult | None = None,
    plan: _FakePlan | None = None,
    compose: _FakeCompose | None = None,
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
            plan=plan or _FakePlan(_ANSWERED_EVIDENCE),
            compose=compose or _FakeCompose(WHOLE),
            compose_stream=compose_stream,
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

    orch, _ = _build(compose_stream=_FakeComposeStream())

    events = await _collect(orch)

    kinds = [type(event).__name__ for event in events]
    assert kinds == [
        "TurnStarted",
        "ClaimDelta",
        "ClaimDelta",
        "ClaimDelta",
        "Claim",
        "TurnFinished",
    ]


async def test_the_pieces_join_back_to_the_answer_that_is_recorded() -> None:
    """What the farmer read as it arrived and what the turn is stored as must
    be the same words — otherwise the stream showed something the record
    denies."""

    orch, turns = _build(compose_stream=_FakeComposeStream())

    events = await _collect(orch)

    streamed = "".join(e.text for e in events if isinstance(e, ClaimDelta))
    claim = next(e for e in events if isinstance(e, Claim))
    finished = events[-1]
    assert streamed == WHOLE
    assert isinstance(claim.content, TextBlock)
    assert claim.content.text == WHOLE
    assert finished.content[0].text == WHOLE
    assert turns.records["t1"].finished is finished


async def test_the_terminal_event_is_unchanged_by_streaming() -> None:
    """Streaming is a delivery change, not an answer change. Everything a
    non-streaming caller would have received still arrives."""

    streamed_events = await _collect(_build(compose_stream=_FakeComposeStream())[0])
    whole_events = await _collect(_build(compose_stream=None)[0])

    streamed_end, whole_end = streamed_events[-1], whole_events[-1]
    assert isinstance(streamed_end, TurnFinished)
    assert streamed_end.outcome == whole_end.outcome
    assert streamed_end.content == whole_end.content
    assert streamed_end.sources == whole_end.sources


async def test_an_unwired_stream_leaves_the_whole_answer_path_alone() -> None:
    """The existing endpoint's runner. No deltas, and the composer it was
    given is the one that runs."""

    compose = _FakeCompose(WHOLE)
    orch, _ = _build(compose_stream=None, compose=compose)

    events = await _collect(orch)

    assert not any(isinstance(e, ClaimDelta) for e in events)
    assert compose.calls == 1
    assert [type(e).__name__ for e in events] == [
        "TurnStarted",
        "Claim",
        "TurnFinished",
    ]


async def test_a_refused_turn_streams_nothing() -> None:
    """A refusal is a fixed reply, not something the model writes, so there is
    nothing to release early — and the composer must never be spent on one."""

    stream = _FakeComposeStream()
    empty = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(compose_stream=stream, discovery=empty)

    events = await _collect(orch)

    assert events[-1].outcome.status is TurnStatus.NO_MATCH
    assert not any(isinstance(e, ClaimDelta) for e in events)
    assert stream.calls == 0


async def test_a_failure_part_way_through_leaves_the_pieces_already_sent() -> None:
    """No retry and no rollback: the runner cannot un-send them. It propagates,
    and the transport turns it into a failed turn."""

    orch, _ = _build(compose_stream=_FakeComposeStream(fail_after=2))
    seen = []

    with pytest.raises(RuntimeError, match="stream dropped"):
        async for event in orch.run(_turn(), _ctx()):
            seen.append(event)

    assert [e.text for e in seen if isinstance(e, ClaimDelta)] == ["Wheat is ", "2,2"]
    assert not any(isinstance(e, TurnFinished) for e in seen)
    assert isinstance(seen[0], TurnStarted)
