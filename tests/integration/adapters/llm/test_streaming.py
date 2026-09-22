"""Tier 2 — `stream_text` on the Pydantic AI adapter.

The port promises "given a system prompt and a query, yield the answer in
pieces". This asserts the adapter keeps that promise against a model that
really streams: the pieces arrive one at a time, in order, and joining them
back gives the whole answer.

`FunctionModel(stream_function=...)` is the local test model — a real Pydantic
AI streaming path, no network. The chunk boundaries are chosen to split a word
and a number, which is what a live model does and what the transport must
carry unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic_ai.messages import ModelMessage, SystemPromptPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.observability.stages import Stage

# Deliberately mid-word ("quin" / "tal") and mid-number ("1,4" / "50").
CHUNKS = ("Paddy is ", "Rs 1,4", "50 per quin", "tal at Anand.")
WHOLE = "Paddy is Rs 1,450 per quintal at Anand."


class _Streamer:
    """A model that streams fixed chunks and records what it was prompted with."""

    def __init__(self, chunks: tuple[str, ...] = CHUNKS) -> None:
        self._chunks = chunks
        self.prompts: list[str] = []

    def as_model(self) -> FunctionModel:
        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str]:
            self.prompts.extend(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, SystemPromptPart | UserPromptPart)
                and isinstance(part.content, str)
            )
            for chunk in self._chunks:
                yield chunk

        return FunctionModel(stream_function=stream)


def _provider(
    model: FunctionModel, *, debounce: float | None = None
) -> PydanticAILLMProvider:
    """Debouncing off by default here. A test model emits its chunks with no
    gap between them, so the production 100ms window would group every one into
    a single piece and the ordering assertions would prove nothing."""

    return PydanticAILLMProvider(
        model,
        name="composer",
        stage=Stage.COMPOSER,
        stream_debounce_seconds=debounce,
    )


async def _collect(provider: PydanticAILLMProvider, **kwargs: str) -> list[str]:
    return [delta async for delta in provider.stream_text(**kwargs)]


async def test_the_answer_arrives_in_pieces_that_join_back_to_the_whole() -> None:
    """The one guarantee everything downstream rests on: what the farmer reads
    as it streams is exactly what the finished answer says."""

    streamer = _Streamer()

    deltas = await _collect(
        _provider(streamer.as_model()),
        system_prompt="You are a farm advisor.",
        user_query="What is the price of paddy?",
    )

    assert len(deltas) > 1, "a single delta is not a stream"
    assert "".join(deltas) == WHOLE


async def test_the_prompt_and_the_query_both_reach_the_model() -> None:
    streamer = _Streamer()

    await _collect(
        _provider(streamer.as_model()),
        system_prompt="You are a farm advisor.",
        user_query="What is the price of paddy?",
    )

    everything = "\n".join(streamer.prompts)
    assert "You are a farm advisor." in everything
    assert "What is the price of paddy?" in everything


async def test_pieces_arrive_in_the_order_the_model_wrote_them() -> None:
    deltas = await _collect(
        _provider(_Streamer().as_model()),
        system_prompt="You are a farm advisor.",
        user_query="What is the price of paddy?",
    )

    assert deltas == list(CHUNKS)


async def test_debouncing_groups_pieces_without_changing_the_answer() -> None:
    """The production default groups a burst of pieces into fewer frames. It
    must only ever change *how many* pieces arrive, never what they say."""

    deltas = await _collect(
        _provider(_Streamer().as_model(), debounce=0.1),
        system_prompt="You are a farm advisor.",
        user_query="What is the price of paddy?",
    )

    assert len(deltas) < len(CHUNKS)
    assert "".join(deltas) == WHOLE
