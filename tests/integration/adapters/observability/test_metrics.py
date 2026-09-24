"""Tier 2 — the instruments, read back through an in-memory reader.

`set_meter_provider` is one-shot per process, exactly like `set_tracer_provider`,
so every test here builds its own provider and hands it to `configure_metrics`
rather than installing it globally. The instruments are taken away again after,
or they would publish every later test's turns.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from dss.adapters.observability.metrics import (
    LABEL_KEYS,
    configure_metrics,
    record_agent_run,
    record_first_claim,
    record_first_delta,
    record_stage_duration,
    record_stage_tokens,
    record_turn,
    reset_metrics,
)
from dss.observability.stages import Stage


@pytest.fixture
def reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    configure_metrics(
        model_profile="test-profile",
        meter_provider=MeterProvider(metric_readers=[reader]),
    )
    yield reader
    reset_metrics()


def points(reader: InMemoryMetricReader, name: str) -> list:
    """Every data point recorded for one instrument."""

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


def test_nothing_is_published_before_configure(monkeypatch) -> None:
    # Every unit test and every local run without an exporter lands here. It
    # must cost a None check, not raise.
    reset_metrics()
    record_turn(status="answered", elapsed_ms=100.0, cost=0.0)
    record_stage_duration(stage="intent", elapsed_ms=10.0, model="m")
    record_first_delta(20.0)
    record_first_claim(50.0)
    record_stage_tokens(stage=Stage.INTENT, model="m", input_tokens=1, output_tokens=2)


def test_a_turn_publishes_duration_count_and_cost(reader) -> None:
    record_turn(status="answered", elapsed_ms=2500.0, cost=0.004)

    duration = points(reader, "dss.turn.duration")
    assert len(duration) == 1
    # Seconds, not milliseconds — the same dashboard shows
    # `http.server.request.duration`, which OpenTelemetry fixes at seconds.
    assert duration[0].sum == pytest.approx(2.5)
    assert dict(duration[0].attributes) == {
        "status": "answered",
        "model_profile": "test-profile",
    }

    count = points(reader, "dss.turn.count")
    assert count[0].value == 1
    assert dict(count[0].attributes) == {
        "status": "answered",
        "model_profile": "test-profile",
    }

    cost = points(reader, "dss.turn.cost")
    assert cost[0].sum == pytest.approx(0.004)
    # No status on cost: it is the same number whatever the turn returned.
    assert dict(cost[0].attributes) == {"model_profile": "test-profile"}


def test_first_claim_is_its_own_instrument(reader) -> None:
    record_turn(status="answered", elapsed_ms=4000.0, cost=0.0)
    record_first_claim(1200.0)

    first_claim = points(reader, "dss.turn.first_claim.duration")
    assert first_claim[0].sum == pytest.approx(1.2)
    assert dict(first_claim[0].attributes) == {"model_profile": "test-profile"}


def test_first_delta_is_its_own_instrument(reader) -> None:
    record_first_delta(300.0)

    first_delta = points(reader, "dss.turn.first_delta.duration")
    assert first_delta[0].sum == pytest.approx(0.3)
    assert dict(first_delta[0].attributes) == {"model_profile": "test-profile"}


def test_a_stage_with_a_model_carries_it(reader) -> None:
    record_stage_duration(stage="planner", elapsed_ms=800.0, model="azure:gpt-5.6-luna")

    point = points(reader, "dss.stage.duration")[0]
    assert dict(point.attributes) == {
        "stage": "planner",
        "model": "azure:gpt-5.6-luna",
    }


def test_a_stage_with_no_model_carries_no_model_label(reader) -> None:
    # Discovery and enrichment run no model. An absent label reads as "not
    # applicable"; a "none" value would read as a model name.
    record_stage_duration(stage="discovery", elapsed_ms=300.0, model=None)

    point = points(reader, "dss.stage.duration")[0]
    assert dict(point.attributes) == {"stage": "discovery"}


def test_tokens_split_by_direction(reader) -> None:
    record_stage_tokens(
        stage=Stage.COMPOSER, model="m-1", input_tokens=900, output_tokens=120
    )

    by_direction = {
        point.attributes["direction"]: point.value
        for point in points(reader, "dss.stage.tokens")
    }
    assert by_direction == {"input": 900, "output": 120}


def test_no_instrument_publishes_a_label_it_is_not_allowed(reader) -> None:
    """The guard that matters: what is actually emitted, not what is declared.

    `test_metric_labels.py` pins the declared set. This one catches a call site
    passing something the declaration never mentioned.
    """

    record_turn(status="answered", elapsed_ms=1.0, cost=0.1)
    record_first_delta(1.0)
    record_first_claim(1.0)
    record_stage_duration(stage="intent", elapsed_ms=1.0, model="m")
    record_stage_duration(stage="discovery", elapsed_ms=1.0, model=None)
    record_stage_tokens(stage=Stage.INTENT, model="m", input_tokens=1, output_tokens=1)

    for name, allowed in LABEL_KEYS.items():
        for point in points(reader, name):
            assert set(point.attributes) <= set(allowed), name


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens, cost):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost = cost


class _FakeResult:
    """Shaped like a Pydantic AI run result, without running a model.

    `record_agent_run` is duck-typed against exactly these three accessors, so
    this is the contract it depends on.
    """

    def __init__(self, model_name, usage):
        self.response = type("Response", (), {"model_name": model_name})()
        # Properties, not methods — that is how Pydantic AI exposes both.
        self.usage = usage


def test_a_model_run_publishes_its_tokens_and_names_its_model(reader) -> None:
    from decimal import Decimal

    from dss.observability.turn_usage import begin_turn_usage, model_for

    begin_turn_usage()
    record_agent_run(
        stage=Stage.INTENT,
        result=_FakeResult("gpt-4o-mini", _FakeUsage(120, 30, Decimal("0.0007"))),
    )

    by_direction = {
        point.attributes["direction"]: point.value
        for point in points(reader, "dss.stage.tokens")
    }
    assert by_direction == {"input": 120, "output": 30}
    # The model that answered, so `dss.stage.duration` can carry it too.
    assert model_for(Stage.INTENT) == "gpt-4o-mini"


def test_a_self_hosted_model_reports_no_cost(reader) -> None:
    from dss.observability.turn_usage import begin_turn_usage, current_turn_usage

    begin_turn_usage()
    record_agent_run(
        stage=Stage.COMPOSER,
        result=_FakeResult("local-llama", _FakeUsage(50, 10, None)),
    )

    usage = current_turn_usage()
    assert usage is not None
    # No published price, so no cost. Tokens are the number that matters.
    assert usage.cost == 0.0
