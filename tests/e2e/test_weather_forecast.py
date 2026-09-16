"""Tier 7 — "is it going to rain today in Nashik?", end to end.

The third capability, and the one where the search origin is a *filterable*
field. `WeatherObservation` lists `location.geo`, so unlike a facility search
the model can write the location itself — and it wrote the place name where the
pack asks for a GeoJSON geometry, which the provider refused.

It could not have done better. `_flatten` descended past `location.geo` into
`location.geo.type`, which matches no filterable path, so the *correct* shape
failed validation while the bare name passed and lost at the provider.

Same gate as the other two: no key in the environment, no run.
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

pytestmark = pytest.mark.skipif(
    not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="live model test — export AZURE_OPENAI_API_KEY and "
    "AZURE_OPENAI_ENDPOINT (MODEL below is an Azure deployment), or "
    "OPENAI_API_KEY for a deployment bound the other way",
)

MODEL = "azure:gpt-5.6-luna"
QUERY = "Is it going to rain today in Nashik?"

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"

# Nashik. The point the forecast is for, and the value the model overwrote with
# the word "Nashik".
ORIGIN = [73.7898, 19.9975]


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


DISCOVER_RESPONSE = _load("discover_response_weather.json")
SELECT_RESPONSE = _load("select_response_weather.json")

# Read from the fixtures, not restated: what the provider advertises is what
# the assertions below expect, by construction.
_RESOURCE = DISCOVER_RESPONSE["message"]["catalogs"][0]["resources"][0]
CAPABILITY = _RESOURCE["resourceAttributes"]["@type"]
SUBJECT_CATEGORY = _RESOURCE["resourceAttributes"]["subjectCategories"][0]
PROVIDER_ID = DISCOVER_RESPONSE["message"]["catalogs"][0]["provider"]["id"]

_ANSWER = SELECT_RESPONSE["message"]["contract"]["commitments"][0]["resources"][0]
EXPECTED_RAINFALL = next(
    parameter["values"]["sum"]
    for parameter in _ANSWER["resourceAttributes"]["parameters"]
    if parameter["parameter"] == "Rainfall"
)


def _geometry(select_body: dict[str, Any]) -> dict[str, Any] | None:
    """The geometry the select asks the forecast for, if it sent one at all.

    Returns whatever sits under `location.geo` rather than only a dict, so a
    string — which is what the model wrote — reaches the assertion instead of
    reading as absent.
    """

    attributes = select_body["message"]["contract"]["commitments"][0]["resources"][0][
        "resourceAttributes"
    ]
    return (attributes.get("location") or {}).get("geo")


class _Network:
    """The OAN network adapter, faked. Records what the DSS sent.

    Handlers rather than `respond_with_json`, because each response depends on
    the request: answering regardless would let a select carrying a place name
    still pass, which is the defect this test exists for.
    """

    def __init__(self) -> None:
        self.discover_requests: list[dict[str, Any]] = []
        self.select_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.discover_requests.append(body)
        expression = body["message"]["intent"]["filters"]["expression"]
        context = " ".join(body["context"].get("schemaContext", ()))
        if SUBJECT_CATEGORY not in expression or CAPABILITY not in context:
            return self._json({"message": {"catalogs": []}})
        return self._json(DISCOVER_RESPONSE)

    def select(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.select_requests.append(body)
        geometry = _geometry(body)
        # What the provider itself checks: a GeoJSON geometry, not a word. The
        # real NACK was `value must be an object`.
        if not isinstance(geometry, dict) or "coordinates" not in geometry:
            return self._json(
                {
                    "error": "location.geo must be a GeoJSON geometry",
                    "received": body["message"]["contract"]["commitments"][0][
                        "resources"
                    ][0]["resourceAttributes"],
                },
                status=400,
            )
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


def _ask(client: TestClient, a_body, query: str):
    return client.post(
        "/v1/turns",
        json=a_body(
            message__input=[
                {"role": "user", "content": [{"type": "text", "text": query}]}
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


def test_a_live_model_answers_the_rainfall_forecast(
    live_app, live_network, a_body
) -> None:
    response = _ask(TestClient(live_app), a_body, QUERY)

    assert response.status_code == 200
    message = response.json()["message"]

    # Printed before the first assertion: when the provider refuses, the body
    # it refused is the only thing that explains why.
    if live_network.select_requests:
        print(
            "\n  select sent: "
            + json.dumps(
                live_network.select_requests[-1]["message"]["contract"]["commitments"][
                    0
                ]["resources"][0]["resourceAttributes"],
                indent=2,
            )
        )

    assert message["outcome"]["status"] == "answered", message["outcome"]

    # Intent, read off the wire. Only `Weather` reaches this capability in the
    # schema-pack index, so the filter and the context together prove the
    # classification.
    assert live_network.discover_requests, "discovery never ran"
    discover = live_network.discover_requests[0]
    expression = discover["message"]["intent"]["filters"]["expression"]
    assert SUBJECT_CATEGORY in expression, expression
    assert CAPABILITY in " ".join(discover["context"]["schemaContext"]), discover[
        "context"
    ]

    assert live_network.select_requests, "the provider was never called"

    # The point the forecast is for. `location.geo` is filterable here, so the
    # model can write it — and it wrote the word "Nashik" over the coordinates
    # the turn already carried.
    assert _geometry(live_network.select_requests[0]) == {
        "type": "Point",
        "coordinates": ORIGIN,
    }

    # `/select` went to the provider `/discover` offered.
    offer = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "offer"
    ]
    assert offer["provider"]["id"] == PROVIDER_ID

    # The composer wrote the provider's number, not its own. Digits only —
    # wording and units are the model's choice.
    text = " ".join(block.get("text", "") for block in message["content"])
    print(f"\n  model wrote: {text!r}")
    assert str(EXPECTED_RAINFALL) in text, text
