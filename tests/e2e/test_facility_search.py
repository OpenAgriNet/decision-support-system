"""Tier 7 — "what are the krishi kendra near me?", end to end.

A second capability beside `test_onion_price.py`, and a different shape of ask.
A mandi price names a market and a commodity; a facility search names a point
and asks what is around it. The provider ranks by distance from that point, so
the select carrying it is the whole question.

`AgricultureFacility` declares `location` and leaves it out of
`filterable_paths` — the search origin is not a filter over advertised values —
and a select built from the filterable list alone dropped it. The provider then
had nothing to search around.

Same gate as the mandi test: no key in the environment, no run.
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
QUERY = "What are the krishi kendra near me?"

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"

# Nashik. The farmer's point, and the only thing that says where to search.
ORIGIN = [73.7898, 19.9975]


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


DISCOVER_RESPONSE = _load("discover_response_facility.json")
SELECT_RESPONSE = _load("select_response_facility.json")

# Read from the fixtures, not restated: what the provider advertises is what
# the assertions below expect, by construction.
_RESOURCE = DISCOVER_RESPONSE["message"]["catalogs"][0]["resources"][0]
CAPABILITY = _RESOURCE["resourceAttributes"]["@type"]
SUBJECT_CATEGORY = _RESOURCE["resourceAttributes"]["subjectCategories"][0]
PROVIDER_ID = DISCOVER_RESPONSE["message"]["catalogs"][0]["provider"]["id"]
WANTED_TYPE = "KrishiVigyanKendra"

_ANSWER = SELECT_RESPONSE["message"]["contract"]["commitments"][0]["resources"][0]
EXPECTED_NAME = _ANSWER["resourceAttributes"]["facilityName"]
EXPECTED_LOCALITY = _ANSWER["resourceAttributes"]["address"]["addressLocality"]


def _search_origin(select_body: dict[str, Any]) -> list[float] | None:
    """The point the select asks the provider to search around."""

    attributes = select_body["message"]["contract"]["commitments"][0]["resources"][0][
        "resourceAttributes"
    ]
    geo = (attributes.get("location") or {}).get("geo") or {}
    coordinates = geo.get("coordinates")
    return coordinates if isinstance(coordinates, list) else None


class _Network:
    """The OAN network adapter, faked. Records what the DSS sent.

    Handlers rather than `respond_with_json`, because each response depends on
    the request: answering regardless would let a select with no search origin
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
        origin = _search_origin(body)
        if origin is None:
            # 400, not 404: `classify_status_code` treats only 400/401/403 as a
            # defect, so a 404 would be retried three times before failing.
            return self._json(
                {
                    "error": "this Resource searches around a point; none was sent",
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


def test_a_live_model_finds_a_nearby_facility(live_app, live_network, a_body) -> None:
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

    # Intent, read off the wire. Only `Facility` reaches this capability in the
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
    sent = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "resources"
    ][0]["resourceAttributes"]

    # The search origin. `AgricultureFacility` declares `location` and does not
    # list it as filterable, so a select built from the filterable paths alone
    # dropped it and the provider had no point to rank distances from.
    assert _search_origin(live_network.select_requests[0]) == ORIGIN, sent

    # The model read "krishi kendra" and named the governed type.
    assert sent.get("facilityType") == WANTED_TYPE, sent

    # `/select` went to the provider `/discover` offered.
    offer = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "offer"
    ]
    assert offer["provider"]["id"] == PROVIDER_ID

    # The composer wrote what the provider returned, not its own knowledge.
    text = " ".join(block.get("text", "") for block in message["content"])
    print(f"\n  model wrote: {text!r}")
    assert EXPECTED_NAME.split(",")[0] in text, text
    assert EXPECTED_LOCALITY in text, text
