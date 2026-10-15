"""Tier 7 — a Gujarati weather question, through the stub translator.

The stub turns any Gujarati turn into "Will it rain today?" and marks what it
sends back `[gu]`, so this runs the whole frame — translate in, the real
pipeline with live models and the mocked network, translate out — before a
translation model exists.

Same gate as the other live tests: no key in the environment, no run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer

from dss.config.settings import Settings
from dss.entrypoint.app import build_app
from dss.entrypoint.composition import build_runner
from tests.e2e.test_weather_forecast import MODEL, ORIGIN, SCHEMA_PACKS, _Network

pytestmark = pytest.mark.skipif(
    not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="live model test — export AZURE_OPENAI_API_KEY and "
    "AZURE_OPENAI_ENDPOINT, or OPENAI_API_KEY",
)

# Written here rather than read from configs/: the shipped entry moves to the
# LLM translator, and this test is about the stub.
STUB_GUJARATI = "languages:\n  gu: {to: en, adapter: stub}\n"


@pytest.fixture
def live_network(httpserver: HTTPServer) -> _Network:
    network = _Network()
    httpserver.expect_request("/discover").respond_with_handler(network.discover)
    httpserver.expect_request("/select").respond_with_handler(network.select)
    return network


@pytest.fixture
def live_app(httpserver: HTTPServer, live_network: _Network, tmp_path: Path):
    translation_config = tmp_path / "edge-translation.yaml"
    translation_config.write_text(STUB_GUJARATI, encoding="utf-8")
    base_url = httpserver.url_for("").rstrip("/")
    settings = Settings(
        stub_llm=False,
        intent_model=MODEL,
        moderation_model=MODEL,
        planner_model=MODEL,
        composer_model=MODEL,
        discovery_base_url=base_url,
        invocation_base_url=base_url,
        schema_pack_dir=SCHEMA_PACKS,
        evidence_dir=tmp_path,
        edge_translation_config_path=translation_config,
    )
    return build_app(runner=build_runner(settings), settings=settings)


def test_a_gujarati_turn_is_answered_through_the_stub(
    live_app, live_network, a_body
) -> None:
    response = TestClient(live_app).post(
        "/v1/turns",
        json=a_body(
            message__input=[
                {"role": "user", "content": [{"type": "text", "text": "આજે વરસાદ પડશે?"}]}
            ],
            message__attributes={
                "sourceLanguage": "gu",
                "targetLanguage": "gu",
                "channel": "web",
                "location": {
                    "region": "IN-MH",
                    "area": "Nashik",
                    "geometry": {"type": "Point", "coordinates": ORIGIN},
                },
                "response": {"maxCharacters": 1200},
            },
        ),
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    message = response.json()["message"]
    assert message["outcome"]["status"] == "answered", message["outcome"]
    assert live_network.select_requests, "the provider was never called"
    texts = [block.get("text", "") for block in message["content"]]
    print(f"\n  farmer reads: {texts!r}")
    assert texts
    assert all(text.startswith("[gu] ") for text in texts)
