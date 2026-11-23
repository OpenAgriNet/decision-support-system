"""Tier 2 — what a strict tool call puts on the wire.

A small model leaves out a field the schema lets it leave out, and a field
left out reads the same as "none".
"""

from __future__ import annotations

from pydantic import BaseModel
from pytest_httpserver import HTTPServer

from dss.adapters.llm.pydantic_ai_provider import (
    PydanticAILLMProvider,
    build_gateway_model,
)
from dss.observability.stages import Stage
from tests.support.model_server import FakeModelServer


class _Verdict(BaseModel):
    label: str
    note: str | None = None


async def test_strict_tool_requires_every_field(httpserver: HTTPServer) -> None:
    server = FakeModelServer({"label": "fine", "note": None})
    httpserver.expect_request("/v1/chat/completions").respond_with_handler(server.chat)
    provider = PydanticAILLMProvider(
        build_gateway_model(
            "dss-intent", base_url=httpserver.url_for("/v1"), api_key="key"
        ),
        name="intent-classifier",
        stage=Stage.INTENT,
        output_mode="strict_tool",
    )

    await provider.structured(system_prompt="s", user_query="q", schema=_Verdict)

    assert server.output_tool["strict"] is True
    assert set(server.output_tool["parameters"]["required"]) == {"label", "note"}
