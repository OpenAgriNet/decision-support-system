"""Tier 7 — one turn end to end, against a live model.

It makes real model calls, so it runs only when credentials are present and
skips otherwise — see the `pytestmark` below. To run it:

    set -a; source .env; set +a
    uv run pytest tests/e2e/test_onion_price.py -q --no-cov -s

`OPENAI_API_KEY` and `OPENAI_BASE_URL` have to be in the environment, not just
in `.env`: `pydantic-settings` reads that file into `Settings` and never into
`os.environ`, and the model SDK only reads `os.environ` — hence the `source`.
An IDE run configuration needs them in its own environment-variables field for
the same reason. `OPENAI_BASE_URL` is the base the SDK appends paths to, so it
ends at `/v1`, not at an endpoint like `/v1/responses`.

Nothing is stubbed but the OAN network. All four model calls — intent,
moderation, planner and composer — go to a real provider, so this is the only
test that answers "does a real model actually drive this pipeline". The network
stays local because the point is the model's behaviour, not the network's, and
because a real provider would not return a value we can assert on.

**How intent is asserted without reading it.** The HTTP response carries no
intent, so the outbound requests stand in for it, and they are stronger
evidence:

- `/discover`'s jsonpath filter naming the capability can only happen if intent
  resolved `subject_categories=Market` with `interaction_type=observe` — that is
  the only pair the schema-pack index maps to it.
- `/select`'s commodity carrying onion can only happen if the planner read
  "onion" out of the farmer's sentence and matched it to a field the pack
  declares filterable.

**The sentinel.** The modal price in `fixtures/select_response.json` is
deliberately not a plausible onion price. A realistic number would leave "the
composer echoed our data" indistinguishable from "the model recited something it
already knew".

Everything the fake network returns lives in `fixtures/`, and the values this
test expects are read back out of those files rather than restated here — so a
fixture edit cannot leave the assertions describing something else.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

from dss.config.settings import Settings
from dss.entrypoint.app import build_app
from dss.entrypoint.composition import build_runner

# Gated on the credentials, not on a marker. `pyproject.toml`'s `addopts` carries
# `-m "not eval"`, so an `eval`-marked test is *deselected* by every plain
# `pytest` invocation — including the ones IDE run configurations generate, where
# adding `-m eval` back is fiddly and easy to get silently wrong (a deselected
# test reports as an empty suite, not as a skip).
#
# The credentials gate does the same job more honestly: no key in the
# environment, no run. CI has none, so it skips there. It runs where someone has
# deliberately supplied a key — an export, or an IDE run configuration — which
# is exactly when you want it to.
#
# `AZURE_OPENAI_API_KEY` first, because `MODEL` below names an Azure deployment:
# the gate used to check `OPENAI_API_KEY` alone, so an Azure-bound developer
# could never open it and the test silently skipped for everyone.
pytestmark = pytest.mark.skipif(
    not (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")),
    reason="live model test — export AZURE_OPENAI_API_KEY and "
    "AZURE_OPENAI_ENDPOINT (MODEL below is an Azure deployment), or "
    "OPENAI_API_KEY for a deployment bound the other way",
)

MODEL = "azure:gpt-5.6-luna"
QUERY = "What is the price of Onion at Sholapur mandi this week?"

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PACKS = FIXTURES / "network-specs" / "schema"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


DISCOVER_RESPONSE = _load("discover_response.json")
SELECT_RESPONSE = _load("select_response.json")

# Read from the fixtures, not restated: what discovery advertises is what the
# assertions below expect, by construction.
_RESOURCE = DISCOVER_RESPONSE["message"]["catalogs"][0]["resources"][0]
CAPABILITY = _RESOURCE["resourceAttributes"]["@type"]
PROVIDER_ID = DISCOVER_RESPONSE["message"]["catalogs"][0]["provider"]["id"]
SUBJECT_CATEGORY = _RESOURCE["resourceAttributes"]["subjectCategories"][0]
# Read from the fixture: what the provider advertises is what the select has to
# name. A code, not a word — `supportedCommodities` carries governed codes.
ONION_CODE = next(
    commodity["code"]
    for commodity in _RESOURCE["resourceAttributes"]["supportedCommodities"]
    if commodity["name"] == "Onion"
)

_ANSWER = SELECT_RESPONSE["message"]["contract"]["commitments"][0]["resources"][0]
EXPECTED_MODAL = next(
    price["value"]
    for price in _ANSWER["resourceAttributes"]["prices"]
    if price["priceType"] == "Modal"
)

EXPECTED_MIN = next(
    price["value"]
    for price in _ANSWER["resourceAttributes"]["prices"]
    if price["priceType"] == "Minimum"
)

EXPECTED_MAX = next(
    price["value"]
    for price in _ANSWER["resourceAttributes"]["prices"]
    if price["priceType"] == "Maximum"
)


def _commodity_code(select_body: dict[str, Any]) -> str | None:
    """The commodity code the select asks for.

    Read from `supportedCommodities`, the only commodity field the pack still
    offers — `commodity` was removed from MandiPrice, so a select narrows the
    advertised list instead of naming a separate field. A stale reader here
    found nothing and answered 400 for every call.
    """

    attributes = select_body["message"]["contract"]["commitments"][0]["resources"][0][
        "resourceAttributes"
    ]
    commodities = attributes.get("supportedCommodities")
    if not isinstance(commodities, list) or len(commodities) != 1:
        # More than one means nothing was narrowed: the advertisement came
        # back whole, and the provider cannot tell which was wanted.
        return None
    first = commodities[0]
    return first.get("code") if isinstance(first, dict) else None


class _Network:
    """The OAN network adapter, faked. Records what the DSS sent.

    Handlers rather than `respond_with_json`, because each response depends on
    the request: answering regardless would let a model that ignored the query
    still pass.
    """

    def __init__(self) -> None:
        self.discover_requests: list[dict[str, Any]] = []
        self.select_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.discover_requests.append(body)
        # The filter matches on `subjectCategories`, and `schemaContext` is
        # what names the @type — checking the expression for the @type stopped
        # matching when the filter changed, and this answered an empty catalog
        # for every query, which reads as `no_match`.
        expression = body["message"]["intent"]["filters"]["expression"]
        context = " ".join(body["context"].get("schemaContext", ()))
        if SUBJECT_CATEGORY not in expression or CAPABILITY not in context:
            return self._json({"message": {"catalogs": []}})
        return self._json(DISCOVER_RESPONSE)

    def select(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.select_requests.append(body)
        commodity = _commodity_code(body)
        if commodity != ONION_CODE:
            # 400, not 404: `classify_status_code` treats only 400/401/403 as a
            # defect, so a 404 would be retried three times before failing —
            # slow, and wrong about whose fault it is.
            return self._json(
                {
                    "error": f"this provider serves onion, not {commodity!r}",
                    # Echoed so a failing run says what was actually sent
                    # rather than only what was missing.
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
                # Sholapur — where the market is and where the farmer is
                # asking from. Without it the turn stops at the district
                # question and never reaches a provider, which is right for a
                # located ask and wrong for this test.
                "location": {
                    "region": "IN-MH",
                    "area": "Sholapur",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [75.01386945, 17.89374094],
                    },
                },
                "response": {"maxCharacters": 1200},
            },
        ),
        headers={"Accept": "application/json"},
    )


def test_a_live_model_answers_the_onion_price(live_app, live_network, a_body) -> None:
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

    # Moderation let an ordinary price question through. A refusal would have
    # stopped the turn before discovery.
    assert message["outcome"]["status"] == "answered", message["outcome"]

    # Intent, read off the wire. The filter matches the subject category and
    # `schemaContext` names the resolved @type — only `Market` + `observe`
    # reaches this capability in the schema-pack index, so the pair proves the
    # classification.
    assert live_network.discover_requests, "discovery never ran"
    discover = live_network.discover_requests[0]
    expression = discover["message"]["intent"]["filters"]["expression"]
    assert SUBJECT_CATEGORY in expression, expression
    assert CAPABILITY in " ".join(discover["context"]["schemaContext"]), discover[
        "context"
    ]

    # The planner read "onion" out of the sentence and put it in a field the
    # pack declares filterable. The server would have answered 400 otherwise.
    assert live_network.select_requests, "the provider was never called"
    commodity = _commodity_code(live_network.select_requests[0])
    # The planner read "onion" out of the sentence and narrowed the advertised
    # list to its code. The server would have answered 400 otherwise.
    assert commodity == ONION_CODE, commodity

    # `/select` went to the provider `/discover` offered, not one the model
    # invented.
    offer = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "offer"
    ]
    assert offer["provider"]["id"] == PROVIDER_ID

    # What the discovered resource said about itself survives the round trip.
    # Each of these was wrong against a real provider while this test passed,
    # because the fixture used to advertise nothing but @type — so the select
    # body had nothing to carry through and nothing to get wrong.
    sent = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "resources"
    ][0]["resourceAttributes"]

    # The resource *is* Akluj APMC. A select names a commodity and a date; it
    # does not restate which market, and must not overwrite it — the model
    # wrote the district from the question into `marketName`.
    assert sent["market"] == _RESOURCE["resourceAttributes"]["market"], sent["market"]

    # The model may only send `code`, so the advertised item travels whole.
    assert sent["supportedCommodities"] == [{"code": "23", "name": "Onion"}], sent[
        "supportedCommodities"
    ]

    # Facts about the provider are not filters, and no filterable_path names
    # them.
    for fact in ("historyPeriod", "updateFrequency", "historicalDataAvailable"):
        assert fact not in sent, fact

    # The composer wrote the provider's number, not its own. Digits only —
    # wording, currency placement and thousands separators are the model's
    # choice, and asserting them would make this a coin flip.
    text = " ".join(block.get("text", "") for block in message["content"])
    # Printed so `-s` shows what the model wrote: a drift in tone or length
    # stays visible without an assertion that would be a coin flip.
    print(f"\n  model wrote: {text!r}")
    assert str(EXPECTED_MIN) in re.sub(r"[,\s]", "", text), text
    assert str(EXPECTED_MAX) in re.sub(r"[,\s]", "", text), text
