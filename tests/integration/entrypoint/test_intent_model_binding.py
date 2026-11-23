"""Tier 3 — what the intent model is sent by the binding the app builds.

A local server stands in for the model gateway, so the request is the real
one. No model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pytest_httpserver import HTTPServer

from dss.config.settings import Settings
from dss.core.intent.models import IntentClassification
from dss.entrypoint.composition.root import _intent_llm
from tests.support.model_server import FakeModelServer


async def _ask_schema_sent(httpserver: HTTPServer, tmp_path: Path) -> dict[str, Any]:
    server = FakeModelServer({"asks": [], "confidence": 0.9})
    httpserver.expect_request("/v1/chat/completions").respond_with_handler(server.chat)
    settings = Settings(
        evidence_dir=tmp_path,
        intent_model="dss-intent",
        gateway_url=httpserver.url_for("/v1"),
        gateway_api_key="key",
    )

    await _intent_llm(settings).structured(
        system_prompt="s", user_query="q", schema=IntentClassification
    )

    return server.output_tool["parameters"]["$defs"]["ClassifiedAsk"]


async def test_intent_must_write_every_ask_field(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    """A field the model may skip reads as "none": a scheme code a small
    model left out became "Which scheme?"."""

    ask = await _ask_schema_sent(httpserver, tmp_path)

    assert set(ask["required"]) == set(ask["properties"])


async def test_intent_is_asked_in_short_names(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    """A long name read back by its Python name still parses, so only the
    request shows which names the model is told to write."""

    ask = await _ask_schema_sent(httpserver, tmp_path)

    assert set(ask["properties"]) == {
        "subject",
        "category",
        "interaction",
        "place",
        "from_history",
        "scheme_code",
        "sure",
        "unlisted",
        "action",
    }
