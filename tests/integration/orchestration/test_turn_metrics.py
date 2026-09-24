"""Tier 3 — a real turn through the orchestrator publishes its metrics.

The span tests next door prove the same numbers reach a trace. These prove they
also reach the aggregate, which is the half a release decision reads: a trace
answers "why was this turn slow", a metric answers "is this deployment slower
than the last one".
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from dss.adapters.observability.metrics import configure_metrics, reset_metrics

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
def reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    configure_metrics(
        model_profile="tier3", meter_provider=MeterProvider(metric_readers=[reader])
    )
    yield reader
    reset_metrics()


def points(reader: InMemoryMetricReader, name: str) -> list:
    collected = reader.get_metrics_data()
    if collected is None:
        return []
    return [
        point
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


async def test_an_answered_turn_is_counted_and_timed(reader) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    (count,) = points(reader, "dss.turn.count")
    assert count.value == 1
    assert dict(count.attributes) == {"status": "answered", "model_profile": "tier3"}

    (duration,) = points(reader, "dss.turn.duration")
    assert duration.count == 1
    assert duration.sum >= 0


async def test_the_first_word_is_timed_separately(reader) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("", chunks=("Wheat is ", "2,275 ", "Rs [1].")),
    )

    await _collect(orch)

    # One point per turn, not per delta.
    (first_delta,) = points(reader, "dss.turn.first_delta.duration")
    assert first_delta.count == 1
    assert dict(first_delta.attributes) == {"model_profile": "tier3"}


async def test_the_first_word_lands_before_the_end_of_composition(reader) -> None:
    """`composed` waits for the whole text, so it can never beat the first
    word. Pinned so nobody reads `composed` as time to first word again."""

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("", chunks=("Wheat is ", "2,275 ", "Rs [1].")),
    )

    await _collect(orch)

    (first_delta,) = points(reader, "dss.turn.first_delta.duration")
    (composed,) = points(reader, "dss.turn.composed.duration")
    assert first_delta.sum <= composed.sum


async def test_composition_is_timed_separately(reader) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    (composed,) = points(reader, "dss.turn.composed.duration")
    assert composed.count == 1
    assert dict(composed.attributes) == {"model_profile": "tier3"}


async def test_a_refused_turn_still_counts_and_publishes_neither_timing(reader) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("unused"),
        violated="delete-command",
        policies=(DELETE_COMMAND,),
    )

    await _collect(orch)

    (count,) = points(reader, "dss.turn.count")
    assert dict(count.attributes) == {"status": "rejected", "model_profile": "tier3"}
    # Never reached the composer, so there is no first word to time. Absent,
    # not zero — a zero here would read as "answered instantly".
    assert points(reader, "dss.turn.first_delta.duration") == []
    assert points(reader, "dss.turn.composed.duration") == []


async def test_a_turn_that_crashes_is_still_counted(reader) -> None:
    """The criterion this exists for. A crashed turn never reaches `_finish`,
    which is why the metrics are published from the turn span's exit instead."""

    class _Exploding:
        async def __call__(self, *args, **kwargs):
            raise RuntimeError("planner died")

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_Exploding(),
        compose=_FakeCompose("unused"),
    )

    with pytest.raises(RuntimeError):
        await _collect(orch)

    (count,) = points(reader, "dss.turn.count")
    assert count.value == 1
    assert dict(count.attributes) == {"status": "error", "model_profile": "tier3"}
    (duration,) = points(reader, "dss.turn.duration")
    assert duration.count == 1


async def test_every_stage_publishes_its_duration(reader) -> None:
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    stages = {
        point.attributes["stage"] for point in points(reader, "dss.stage.duration")
    }
    assert stages == {
        "intent",
        "enrichment",
        "moderation",
        "discovery",
        "planner",
        "composer",
    }


async def test_a_stage_with_no_model_carries_no_model_label(reader) -> None:
    """The fakes run no model, so nothing names one for any stage — and a stage
    with no model must simply omit the label rather than invent a value."""

    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("Wheat is 2,275 Rs [1]."),
    )

    await _collect(orch)

    for point in points(reader, "dss.stage.duration"):
        assert "model" not in point.attributes
