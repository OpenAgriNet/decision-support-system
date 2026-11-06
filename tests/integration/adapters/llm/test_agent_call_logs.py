"""Tier 2 — every model call logs a line before it starts and one after it ends."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.observability.stages import Stage


class _Verdict(BaseModel):
    safe: bool


def _agent_lines(caplog, agent: str) -> dict[str, logging.LogRecord]:
    return {r.event: r for r in caplog.records if getattr(r, "agent", None) == agent}


async def test_a_structured_call_logs_before_and_after(caplog) -> None:
    provider = PydanticAILLMProvider(
        TestModel(), name="moderation", stage=Stage.MODERATION
    )

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        await provider.structured(
            system_prompt="judge", user_query="when to sow tomato", schema=_Verdict
        )

    lines = _agent_lines(caplog, "moderation")
    assert lines["agent_call"].model == "test"
    assert lines["agent_done"].status == "ok"
    assert lines["agent_done"].input_tokens > 0
    assert "when to sow tomato" not in caplog.text


async def test_a_stream_logs_after_its_last_piece(caplog) -> None:
    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        yield "Paddy is "
        yield "Rs 1,450."

    provider = PydanticAILLMProvider(
        FunctionModel(stream_function=stream),
        name="composer",
        stage=Stage.COMPOSER,
        stream_debounce_seconds=None,
    )
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        [_ async for _ in provider.stream_text(system_prompt="s", user_query="q")]

    events = [
        r.event for r in caplog.records if getattr(r, "agent", None) == "composer"
    ]
    assert events == ["agent_call", "agent_done"]


async def test_a_failed_call_logs_its_error_type(caplog) -> None:
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise TimeoutError

    provider = PydanticAILLMProvider(
        FunctionModel(broken), name="intent", stage=Stage.INTENT
    )

    with caplog.at_level(logging.INFO, logger="dss.trace"), pytest.raises(TimeoutError):
        await provider.structured(system_prompt="s", user_query="q", schema=_Verdict)

    assert _agent_lines(caplog, "intent")["agent_done"].status == "error:TimeoutError"
