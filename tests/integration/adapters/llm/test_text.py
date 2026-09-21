"""Tier 2 — `text` on the Pydantic AI adapter.

The whole-answer path. Its reason to exist alongside `stream_text` is retries:
this one may safely re-issue a failed call, because nothing has been handed to
the caller yet. The streaming path may not, so the two are different calls
rather than one call drained two ways.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider

ANSWER = "Paddy is Rs 1,450 per quintal at Anand."


class _Answerer:
    def __init__(self, answer: str = ANSWER) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def as_model(self) -> FunctionModel:
        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.prompts.extend(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, SystemPromptPart | UserPromptPart)
                and isinstance(part.content, str)
            )
            return ModelResponse(parts=[TextPart(content=self._answer)])

        return FunctionModel(model)


async def test_the_whole_answer_comes_back_in_one_piece() -> None:
    answerer = _Answerer()
    provider = PydanticAILLMProvider(answerer.as_model(), name="composer")

    answer = await provider.text(
        system_prompt="You are a farm advisor.",
        user_query="What is the price of paddy?",
    )

    assert answer == ANSWER


async def test_the_prompt_and_the_query_both_reach_the_model() -> None:
    answerer = _Answerer()
    provider = PydanticAILLMProvider(answerer.as_model(), name="composer")

    await provider.text(
        system_prompt="You are a farm advisor.",
        user_query="What is the price of paddy?",
    )

    everything = "\n".join(answerer.prompts)
    assert "You are a farm advisor." in everything
    assert "What is the price of paddy?" in everything
