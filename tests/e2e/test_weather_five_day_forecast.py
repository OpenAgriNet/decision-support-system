"""Tier 7 — "weather for the next 5 days for Nashik?", end to end.

The provider answers with one resource per day. The adapter kept only the
first, so the model saw one day and told the farmer the forecast covered only
today.

Same gate as the other e2e tests: no key in the environment, no run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from dss.config.settings import Settings
from dss.entrypoint.app import build_app
from dss.entrypoint.composition import build_runner
from tests.e2e.pack_gate import pack_rejection

pytestmark = pytest.mark.skipif(
    not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="live model test — export AZURE_OPENAI_API_KEY and "
    "AZURE_OPENAI_ENDPOINT (MODEL below is an Azure deployment), or "
    "OPENAI_API_KEY for a deployment bound the other way",
)

MODEL = "azure:gpt-5.6-luna"
QUERY = "Can you give me weather for next 5 days for Nashik?"

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"

ORIGIN = [73.7898, 19.9975]


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


DISCOVER_RESPONSE = _load("discover_response_weather_five_day.json")
# From a real trace, with the last day's maximum raised from 33 to 35.4:
# rounded, 33 is also the third day's, so it could not prove the last day
# reached the model.
SELECT_RESPONSE = _load("select_response_weather_five_day.json")

_DAYS = SELECT_RESPONSE["message"]["contract"]["commitments"][0]["resources"]


def _maximum(day: dict[str, Any]) -> float:
    return next(
        p["values"]["maximum"]
        for p in day["resourceAttributes"]["parameters"]
        if p["parameter"] == "Temperature"
    )


FIRST_DAY_MAXIMUM = _maximum(_DAYS[0])
LAST_DAY_MAXIMUM = _maximum(_DAYS[-1])


class _Network:
    """The OAN network adapter, faked. Records what the DSS sent."""

    def __init__(self) -> None:
        self.select_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        return self._json(DISCOVER_RESPONSE)

    def select(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.select_requests.append(body)
        rejection = pack_rejection(
            body, root=SCHEMA_PACKS, pack_name="WeatherObservation"
        )
        if rejection is not None:
            return rejection
        return self._json(SELECT_RESPONSE)

    @staticmethod
    def _json(body: dict[str, Any], status: int = 200) -> Response:
        return Response(
            json.dumps(body), status=status, content_type="application/json"
        )


@pytest.fixture
def live_network(httpserver: HTTPServer) -> _Network:
    network = _Network()
    httpserver.expect_request("/discover").respond_with_handler(network.discover)
    httpserver.expect_request("/select").respond_with_handler(network.select)
    return network


@pytest.fixture
def live_app(httpserver: HTTPServer, live_network: _Network, tmp_path: Path):
    """The shipped application, assembled by the real `build_runner`."""

    base_url = httpserver.url_for("").rstrip("/")
    settings = Settings(
        stub_llm=False,  # every model call is real
        intent_model=MODEL,
        moderation_model=MODEL,
        planner_model=MODEL,
        composer_model=MODEL,
        discovery_base_url=base_url,
        invocation_base_url=base_url,
        schema_pack_dir=SCHEMA_PACKS,
        evidence_dir=tmp_path,
    )
    return build_app(runner=build_runner(settings), settings=settings)


def _quoted(value: float, text: str) -> bool:
    """As sent, or rounded to a whole degree — rounding is the model's choice."""

    return f"{value:g}" in text or str(round(value)) in text


def test_a_live_model_answers_every_day_of_the_forecast(
    live_app, live_network, a_body
) -> None:
    response = TestClient(live_app).post(
        "/v1/turns",
        json=a_body(
            message__input=[
                {"role": "user", "content": [{"type": "text", "text": QUERY}]}
            ],
            message__attributes={
                "sourceLanguage": "en",
                "targetLanguage": "en",
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

    # Digits only — wording and units are the model's choice. The last day's
    # figure reaches the model only if every resource does.
    text = " ".join(block.get("text", "") for block in message["content"])
    print(f"\n  model wrote: {text!r}")
    assert _quoted(FIRST_DAY_MAXIMUM, text), text
    assert _quoted(LAST_DAY_MAXIMUM, text), text
