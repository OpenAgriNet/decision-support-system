"""Tier 7 — the registry's ranking decides which provider answers (ADR-0020).

Two weather providers serve Nashik with different rainfall numbers. The
request names its sender in `context.senderId`; the DSS asks the stub
registry for that sender's ranking and the `default` row in parallel, and the
farmer reads the first-choice provider's number. Without a sender the default
ranking applies — and it is exclusive, so the provider it does not name is
never called at all.

Same gate as the other e2e tests: no model key in the environment, no run.
"""

from __future__ import annotations

import copy
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
from tests.e2e.test_weather_forecast import (
    MODEL,
    ORIGIN,
    QUERY,
    SCHEMA_PACKS,
    _load,
)

pytestmark = pytest.mark.skipif(
    not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="live model test — export AZURE_OPENAI_API_KEY and "
    "AZURE_OPENAI_ENDPOINT, or OPENAI_API_KEY",
)

FIXTURES = Path(__file__).parent / "fixtures"

DISCOVER_RESPONSE = _load("discover_response_weather.json")
SELECT_RESPONSE = _load("select_response_weather.json")

IMD_ID = DISCOVER_RESPONSE["message"]["catalogs"][0]["provider"]["id"]
IMD_RAINFALL = next(
    parameter["values"]["sum"]
    for parameter in SELECT_RESPONSE["message"]["contract"]["commitments"][0][
        "resources"
    ][0]["resourceAttributes"]["parameters"]
    if parameter["parameter"] == "Rainfall"
)

# The second provider: the same catalog under another identity, answering a
# rainfall number no prose would produce by accident.
VISTAAR_ID = "maharashtra-vistaar"
VISTAAR_NAME = "Maharashtra Vistaar Weather"
VISTAAR_RAINFALL = 77.7

SENDER = "mh-consumer"


def _as_vistaar_catalog() -> dict[str, Any]:
    catalog = copy.deepcopy(DISCOVER_RESPONSE["message"]["catalogs"][0])
    catalog["provider"]["id"] = VISTAAR_ID
    catalog["provider"]["descriptor"] = {"code": "MH-VISTAAR", "name": VISTAAR_NAME}
    for resource in catalog["resources"]:
        resource["id"] = resource["id"].replace(IMD_ID, VISTAAR_ID)
    return catalog


TWO_PROVIDER_DISCOVER = copy.deepcopy(DISCOVER_RESPONSE)
TWO_PROVIDER_DISCOVER["message"]["catalogs"].append(_as_vistaar_catalog())


def _vistaar_select_response() -> dict[str, Any]:
    clone = copy.deepcopy(SELECT_RESPONSE)
    commitment = clone["message"]["contract"]["commitments"][0]
    commitment["offer"]["provider"] = {
        "id": VISTAAR_ID,
        "descriptor": {"name": VISTAAR_NAME},
    }
    for resource in commitment["resources"]:
        resource["id"] = resource["id"].replace(IMD_ID, VISTAAR_ID)
        attributes = resource["resourceAttributes"]
        attributes["source"] = {
            "sourceId": VISTAAR_ID,
            "sourceUri": "https://weather.mahavistaar.in/",
            "sourceName": VISTAAR_NAME,
        }
        for parameter in attributes["parameters"]:
            if parameter["parameter"] == "Rainfall":
                parameter["values"]["sum"] = VISTAAR_RAINFALL
    return clone


VISTAAR_SELECT = _vistaar_select_response()

# The stub registry's rows — the same shape the Sunbird RC entity serves.
DEFAULT_ROW = {
    "senderId": "default",
    "status": "active",
    "preferences": [{"topic": "Weather", "providers": [IMD_ID]}],
}
SENDER_ROW = {
    "senderId": SENDER,
    "status": "active",
    "preferences": [{"topic": "Weather", "providers": [VISTAAR_ID, IMD_ID]}],
}


class _Network:
    """The OAN network and the registry, faked on one server.

    `/select` answers as whichever provider the offer names, so the ranking's
    choice is visible in the answer's rainfall number. The registry search
    answers from the two rows above, like the real one: an unknown sender is
    an empty result, never an error.
    """

    def __init__(self) -> None:
        self.selected_providers: list[str] = []
        self.searched_senders: list[str] = []

    def discover(self, request: Request) -> Response:
        return self._json(TWO_PROVIDER_DISCOVER)

    def select(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        provider = body["message"]["contract"]["commitments"][0]["offer"]["provider"][
            "id"
        ]
        self.selected_providers.append(provider)
        if provider == VISTAAR_ID:
            return self._json(VISTAAR_SELECT)
        return self._json(SELECT_RESPONSE)

    def search(self, request: Request) -> Response:
        asked = json.loads(request.get_data())["filters"]["senderId"]["eq"]
        self.searched_senders.append(asked)
        row = {r["senderId"]: r for r in (DEFAULT_ROW, SENDER_ROW)}.get(asked)
        return self._json({"totalCount": 1 if row else 0, "data": [row] if row else []})

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
    httpserver.expect_request("/api/v1/SenderPreference/search").respond_with_handler(
        network.search
    )
    return network


@pytest.fixture
def live_app(httpserver: HTTPServer, live_network: _Network, tmp_path: Path):
    base_url = httpserver.url_for("").rstrip("/")
    settings = Settings(
        stub_llm=False,
        intent_model=MODEL,
        moderation_model=MODEL,
        planner_model=MODEL,
        composer_model=MODEL,
        discovery_base_url=base_url,
        invocation_base_url=base_url,
        provider_preferences_base_url=base_url,
        schema_pack_dir=SCHEMA_PACKS,
        evidence_dir=tmp_path,
    )
    return build_app(runner=build_runner(settings), settings=settings)


def _ask(client: TestClient, a_body, *, sender_id: str | None):
    overrides: dict[str, Any] = {
        "message__input": [
            {"role": "user", "content": [{"type": "text", "text": QUERY}]}
        ],
        "message__attributes": {
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
    }
    if sender_id is not None:
        overrides["context__senderId"] = sender_id
    return client.post(
        "/v1/turns", json=a_body(**overrides), headers={"Accept": "application/json"}
    )


def test_the_senders_first_choice_answers_the_turn(
    live_app, live_network, a_body
) -> None:
    response = _ask(TestClient(live_app), a_body, sender_id=SENDER)

    assert response.status_code == 200
    message = response.json()["message"]
    assert message["outcome"]["status"] == "answered", message["outcome"]

    # The registry was asked twice, in one turn: the sender and the default.
    assert sorted(set(live_network.searched_senders)) == ["default", SENDER]

    # Every ranked provider was consulted — the unused answer is recorded —
    # and the farmer reads the first choice's number, not IMD's.
    assert set(live_network.selected_providers) == {VISTAAR_ID, IMD_ID}
    text = " ".join(block.get("text", "") for block in message["content"])
    print(f"\n  model wrote: {text!r}")
    assert str(VISTAAR_RAINFALL) in text, text
    assert str(IMD_RAINFALL) not in text, text


def test_a_turn_without_a_sender_rides_the_default_ranking(
    live_app, live_network, a_body
) -> None:
    response = _ask(TestClient(live_app), a_body, sender_id=None)

    assert response.status_code == 200
    message = response.json()["message"]
    assert message["outcome"]["status"] == "answered", message["outcome"]

    # Only the default row was fetched — there was no sender to ask about.
    assert set(live_network.searched_senders) == {"default"}

    # The default ranking is exclusive: the provider it does not name was
    # never called, and the farmer reads IMD's number.
    assert set(live_network.selected_providers) == {IMD_ID}
    text = " ".join(block.get("text", "") for block in message["content"])
    print(f"\n  model wrote: {text!r}")
    assert str(IMD_RAINFALL) in text, text
