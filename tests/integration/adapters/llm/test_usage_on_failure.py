"""Tier 2 — a model call that fails still records what it spent.

A run can fail after paid requests: output validation exhausts its retries,
or the stream drops part-way. Those requests were billed, so their tokens and
cost must reach the turn. Otherwise a failing turn looks cheaper than it was.

`FunctionModel` is Pydantic AI's local test model — the real run path, no
network. Each response carries a fixed usage, so the numbers are known.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from decimal import Decimal

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import BaseModel
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.adapters.observability.metrics import configure_metrics, reset_metrics
from dss.observability.stages import Stage
from dss.observability.turn_usage import begin_turn_usage, model_for


class _Schema(BaseModel):
    label: str


@pytest.fixture
def reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    configure_metrics(
        model_profile="tier2", meter_provider=MeterProvider(metric_readers=[reader])
    )
    begin_turn_usage()
    yield reader
    reset_metrics()


def _tokens(reader: InMemoryMetricReader) -> dict[str, int]:
    collected = reader.get_metrics_data()
    if collected is None:
        return {}
    return {
        point.attributes["direction"]: point.value
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "dss.stage.tokens"
        for point in metric.data.data_points
    }


def _cost() -> float:
    from dss.observability.turn_usage import current_turn_usage

    usage = current_turn_usage()
    assert usage is not None
    return usage.cost


def _never_valid(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Every attempt is billed and every attempt fails validation."""

    return ModelResponse(
        parts=[TextPart("not the schema")],
        usage=RequestUsage(input_tokens=100, output_tokens=10, cost=Decimal("0.001")),
    )


async def test_a_structured_call_that_exhausts_its_retries_records_every_attempt(
    reader,
) -> None:
    provider = PydanticAILLMProvider(
        FunctionModel(_never_valid),
        name="intent-classifier",
        stage=Stage.INTENT,
        retries=1,
        output_mode="prompted",
    )

    with pytest.raises(UnexpectedModelBehavior):
        await provider.structured(system_prompt="s", user_query="q", schema=_Schema)

    # One try plus one retry: two paid requests.
    assert _tokens(reader) == {"input": 200, "output": 20}
    assert _cost() == pytest.approx(0.002)
    # No response ever validated, so the configured model is the best name.
    assert model_for(Stage.INTENT) == "function:_never_valid:"


def _drops_after_two(chunks: tuple[str, ...] = ("Wheat is ", "2,275 ")):
    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        for chunk in chunks:
            yield chunk
        raise RuntimeError("the model stream dropped")

    return stream


def _composer(model: FunctionModel) -> PydanticAILLMProvider:
    return PydanticAILLMProvider(
        model, name="composer", stage=Stage.COMPOSER, stream_debounce_seconds=None
    )


async def test_a_stream_that_drops_mid_answer_records_what_it_used(reader) -> None:
    provider = _composer(FunctionModel(stream_function=_drops_after_two()))

    with pytest.raises(RuntimeError, match="the model stream dropped"):
        async for _ in provider.stream_text(system_prompt="s", user_query="q"):
            pass

    tokens = _tokens(reader)
    assert tokens["input"] > 0
    assert tokens["output"] > 0
    assert model_for(Stage.COMPOSER) is not None
