"""What a turn's model calls tell the turn about themselves.

Cost is a turn-level metric, but it is only known per model call — and those
calls happen in sibling tasks. This is the part that has to survive that.
"""

import anyio
import pytest

from dss.observability.stages import Stage
from dss.observability.turn_usage import (
    add_cost,
    begin_turn_usage,
    clear_turn_usage,
    current_turn_usage,
    model_for,
    record_stage_model,
)


def test_nothing_is_recorded_outside_a_turn():
    # A ContextVar outlives whatever set it, and another module's test may
    # have started a turn in this same context.
    clear_turn_usage()
    # Every unit test and every local run calls the pipeline with no turn
    # started. These must be no-ops, not failures.
    assert current_turn_usage() is None
    add_cost(1.5)
    record_stage_model(Stage.INTENT, "openai:gpt-4o-mini")
    assert model_for(Stage.INTENT) is None


def test_cost_accumulates_across_calls():
    begin_turn_usage()
    add_cost(0.2)
    add_cost(0.3)
    usage = current_turn_usage()
    assert usage is not None
    assert usage.cost == pytest.approx(0.5)


def test_each_stage_keeps_its_own_model():
    begin_turn_usage()
    record_stage_model(Stage.INTENT, "azure:intent-deploy")
    record_stage_model(Stage.MODERATION, "azure:moderation-deploy")
    assert model_for(Stage.INTENT) == "azure:intent-deploy"
    assert model_for(Stage.MODERATION) == "azure:moderation-deploy"


def test_a_sibling_task_writes_back_to_the_turn():
    """Intent and moderation run as siblings under one task group.

    A ContextVar holding a plain value would give each child its own copy, and
    the parent would drain a cost of zero. The var holds one mutable object
    instead, so a child mutates what the parent reads.
    """

    async def scenario() -> None:
        begin_turn_usage()

        async def stage(name: Stage, cost: float) -> None:
            add_cost(cost)
            record_stage_model(name, f"model-for-{name}")

        async with anyio.create_task_group() as tg:
            tg.start_soon(stage, Stage.INTENT, 0.1)
            tg.start_soon(stage, Stage.MODERATION, 0.4)

        usage = current_turn_usage()
        assert usage is not None
        assert usage.cost == pytest.approx(0.5)
        assert model_for(Stage.INTENT) == "model-for-intent"
        assert model_for(Stage.MODERATION) == "model-for-moderation"

    anyio.run(scenario)


def test_a_new_turn_does_not_inherit_the_last_one():
    begin_turn_usage()
    add_cost(5.0)
    begin_turn_usage()
    usage = current_turn_usage()
    assert usage is not None
    assert usage.cost == 0.0
