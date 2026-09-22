"""Tier 3 — what the `dss.turn` root says about the turn it covers.

Three numbers a stage span cannot give. `status` is the turn's outcome, which
is only known at the end. The two timings are about when the farmer first heard
anything: a turn streams, so an answer starts arriving long before the turn
finishes, and the gap between the composer starting and its first word is
almost all of what a composer span measures.

Absent, never zero. Four outcomes never reach the composer — refused,
needs-input, no-match, and an early crash — and produce no claim and no delta.
Zero would make "refused in 40ms" indistinguishable from "answered instantly",
and would drag any average down by exactly the refusal rate.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dss.adapters.observability.tracing import open_span
from dss.core.shared.models import ClaimDelta
from dss.observability.trace_log import set_stage_span_opener

from .test_orchestrator import (
    _ANSWERED_EVIDENCE,
    DELETE_COMMAND,
    _build,
    _collect,
    _FakeCompose,
    _FakePlan,
    _one_ask,
    _served_discovery,
)


@pytest.fixture
def spans(monkeypatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    set_stage_span_opener(open_span)
    yield exporter
    set_stage_span_opener(None)


def _turn_span(spans: InMemorySpanExporter):
    (span,) = [s for s in spans.get_finished_spans() if s.name == "dss.turn"]
    return span


async def test_an_answered_turn_carries_its_status_and_both_timings(spans) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    attributes = _turn_span(spans).attributes
    assert attributes["status"] == "answered"
    assert attributes["first_delta_ms"] >= 0
    assert attributes["first_claim_ms"] >= 0


async def test_the_first_claim_lands_after_the_whole_stream(spans) -> None:
    """What `first_claim_ms` actually measures, pinned so nobody reads it as a
    mid-stream moment.

    A claim carries its sources, and sources are resolved from the *complete*
    text — so the first claim cannot exist until the last delta has arrived.
    The number is therefore composition end, and it is the *gap* between the
    two that is worth reading: `first_delta_ms` is how long the farmer waited
    to see anything, and the difference is how long the writing took.
    """

    slow_tail = _FakeCompose("", chunks=("Wheat is ", "2,275 ", "Rs [1]."))
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=slow_tail,
    )

    events = await _collect(orch)

    attributes = _turn_span(spans).attributes
    deltas = [e for e in events if isinstance(e, ClaimDelta)]
    assert len(deltas) == 3
    assert attributes["first_claim_ms"] >= attributes["first_delta_ms"]


async def test_a_refused_turn_has_a_status_and_neither_timing(spans) -> None:
    """Refused turns never reach the composer, so there is no first word to
    time. Absent, not zero — zero here would say the farmer got an answer
    instantly, and would pull any average down by the refusal rate."""

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("unused"),
        violated="delete-command",
        policies=(DELETE_COMMAND,),
    )

    await _collect(orch)

    attributes = _turn_span(spans).attributes
    assert attributes["status"] == "rejected"
    assert "first_delta_ms" not in attributes
    assert "first_claim_ms" not in attributes


async def test_a_composer_that_fails_mid_write_has_a_delta_but_no_claim(spans) -> None:
    """Why these are two fields and not one. The farmer heard the first words,
    so `first_delta_ms` is real and worth keeping. No claim was ever assembled,
    so `first_claim_ms` is absent.

    `status` is still set. A turn that crashed never reaches `_finish`, so
    nothing maps it to a `TurnStatus` — but leaving the attribute off would
    make a crashed turn vanish from any breakdown by status, which is the one
    place it most needs to appear."""

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("", chunks=("Wheat is ", "2,275 Rs."), fail_after=1),
    )

    with pytest.raises(RuntimeError, match="the model stream dropped"):
        await _collect(orch)

    attributes = _turn_span(spans).attributes
    assert attributes["first_delta_ms"] >= 0
    assert "first_claim_ms" not in attributes
    assert attributes["status"] == "error"


async def test_the_model_names_are_on_every_turn(spans, monkeypatch) -> None:
    """Which model answered is the first thing asked of a turn that answered
    badly, and the four are separately configurable (ADR-0004). They are
    registered once at startup rather than passed per turn: they are the same
    on every turn of a process."""

    monkeypatch.setattr(
        "dss.adapters.observability.tracing._model_names",
        {"intent_model": "openai:gpt-4o-mini", "composer_model": "openai:gpt-4o"},
    )
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    attributes = _turn_span(spans).attributes
    assert attributes["intent_model"] == "openai:gpt-4o-mini"
    assert attributes["composer_model"] == "openai:gpt-4o"
