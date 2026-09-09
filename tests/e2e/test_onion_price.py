"""Tier 7 — one turn end to end, against a live model.

Run deliberately, not on every commit — it makes real model calls, so it is
marked `eval`, the one marker `pyproject.toml` keeps out of the default run:

    set -a; source .env; set +a
    uv run pytest -m eval -q --no-cov -s

Needs `OPENAI_API_KEY` and `OPENAI_BASE_URL` in the environment, not just in
`.env`: `pydantic-settings` reads that file into `Settings` and never into
`os.environ`, and the model SDK only reads `os.environ` — hence the `source`
above. The test skips rather than fails when the key is absent.

Nothing is stubbed but the OAN network. All four model calls — intent,
moderation, planner and composer — go to a real provider, so this is the only
test that answers "does a real model actually drive this pipeline". The network
stays local because the point is the model's behaviour, not the network's, and
because a real provider would not return a value we can assert on.

**How intent is asserted without reading it.** The HTTP response carries no
intent, so the outbound requests stand in for it, and they are stronger
evidence:

- `/discover`'s jsonpath filter naming `openagrinet:MandiPrice` can only happen
  if intent resolved `subject_categories=Market` with `interaction_type=observe`
  — that is the only pair the schema-pack index maps to that capability.
- `/select`'s `resourceAttributes.commodity.code` carrying onion can only happen
  if the planner read "onion" out of the farmer's sentence and matched it to a
  field the pack declares filterable.

**The sentinel.** `_SENTINEL_MODAL` is deliberately not a plausible onion price.
A realistic number would leave "the composer echoed our data" indistinguishable
from "the model recited something it already knew".
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

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="live model test — set OPENAI_API_KEY (a .env file is read too)",
    ),
]

MODEL = "azure:gpt-5.6-luna"
QUERY = "what is the price of onion"

SCHEMA_PACKS = Path(__file__).parent / "fixtures" / "network-specs" / "schema"

CAPABILITY = "openagrinet:MandiPrice"
PROVIDER_ID = "agmarknet"
RESOURCE_ID = "res:agmarknet:daily-price"

# Not a plausible onion price. See the module docstring.
_SENTINEL_MODAL = 2847
_SENTINEL_MIN = 2801
_SENTINEL_MAX = 2893

_CONTEXT_URL = (
    "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
)


def _discover_response() -> dict[str, Any]:
    return {
        "context": {"action": "on_discover", "version": "2.0.0"},
        "message": {
            "catalogs": [
                {
                    "id": "cat:agmarknet:mandi-price",
                    "descriptor": {"code": "AGMARKNET-01", "name": "Agmarknet"},
                    "provider": {
                        "id": PROVIDER_ID,
                        "descriptor": {"code": "AGMARKNET-01", "name": "Agmarknet"},
                        "availableAt": [
                            {
                                "geo": {
                                    "type": "Polygon",
                                    "coordinates": [
                                        [
                                            [68.1, 8.0],
                                            [97.4, 8.0],
                                            [97.4, 37.1],
                                            [68.1, 37.1],
                                            [68.1, 8.0],
                                        ]
                                    ],
                                }
                            }
                        ],
                    },
                    "resources": [
                        {
                            "id": RESOURCE_ID,
                            "descriptor": {"name": "Daily mandi price"},
                            "resourceAttributes": {
                                "@context": _CONTEXT_URL,
                                "@type": CAPABILITY,
                                "informationMode": "OnDemand",
                                "subjectCategories": ["Market"],
                            },
                        }
                    ],
                }
            ]
        },
    }


def _select_response() -> dict[str, Any]:
    return {
        "context": {"action": "on_select", "version": "2.0.0"},
        "message": {
            "contract": {
                "commitments": [
                    {
                        "status": {"descriptor": {"code": "ACTIVE", "name": "Active"}},
                        "resources": [
                            {
                                "id": "res:agmarknet:price:2026-08-26",
                                "resourceAttributes": {
                                    "@context": _CONTEXT_URL,
                                    "@type": CAPABILITY,
                                    "informationMode": "Direct",
                                    "subjectCategories": ["Market"],
                                    "commodity": {"code": "ONION", "name": "Onion"},
                                    "market": {
                                        "marketCode": "MH-LASALGAON-01",
                                        "marketName": "Lasalgaon",
                                        "state": "Maharashtra",
                                    },
                                    "arrivalDate": "2026-08-26",
                                    "prices": [
                                        {
                                            "priceType": "Minimum",
                                            "unit": "INR/quintal",
                                            "value": _SENTINEL_MIN,
                                        },
                                        {
                                            "priceType": "Modal",
                                            "unit": "INR/quintal",
                                            "value": _SENTINEL_MODAL,
                                        },
                                        {
                                            "priceType": "Maximum",
                                            "unit": "INR/quintal",
                                            "value": _SENTINEL_MAX,
                                        },
                                    ],
                                },
                            }
                        ],
                        "offer": {
                            "id": "offer:agmarknet:open-data",
                            "resourceIds": ["res:agmarknet:price:2026-08-26"],
                            "provider": {
                                "id": PROVIDER_ID,
                                "descriptor": {
                                    "code": "AGMARKNET-01",
                                    "name": "Agmarknet",
                                },
                            },
                        },
                    }
                ]
            }
        },
    }


class _Network:
    """Records what the DSS sent, and answers conditionally.

    A handler rather than `respond_with_json`, because the response depends on
    the request: `/select` must only produce a price when the planner actually
    asked for onion. Answering regardless would let a planner that ignored the
    query still pass.
    """

    def __init__(self) -> None:
        self.discover_requests: list[dict[str, Any]] = []
        self.select_requests: list[dict[str, Any]] = []

    def discover(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.discover_requests.append(body)
        expression = body["message"]["intent"]["filters"]["expression"]
        if CAPABILITY not in expression:
            return Response(
                json.dumps({"message": {"catalogs": []}}), content_type="text/json"
            )
        return Response(
            json.dumps(_discover_response()), content_type="application/json"
        )

    def select(self, request: Request) -> Response:
        body = json.loads(request.get_data())
        self.select_requests.append(body)
        commodity = self._commodity_code(body)
        if commodity is None or "onion" not in commodity.lower():
            # 400, not 404: `classify_status_code` treats only 400/401/403 as a
            # defect, so a 404 would be retried three times before failing —
            # slow, and wrong about whose fault it is.
            return Response(
                json.dumps({"error": f"this provider serves onion, not {commodity!r}"}),
                status=400,
                content_type="application/json",
            )
        return Response(json.dumps(_select_response()), content_type="application/json")

    @staticmethod
    def _commodity_code(body: dict[str, Any]) -> str | None:
        """Read the commodity from either shape the DSS may send.

        Tolerant on purpose, and it should not have to be. A pack's
        `filterable_paths` are dotted JSON paths
        (`beckn:resourceAttributes.commodity.code`) describing a *nested*
        document, but `describe_capability` shows the model those dotted
        strings and nothing converts what comes back. `validate_arguments`
        accepts both, because `_flatten` reduces the nested form to the same
        dotted path — so the DSS emits `{"commodity": {"code": ...}}` or
        `{"commodity.code": ...}` depending on which the model picked, and a
        provider expecting the Beckn shape would only understand one.

        This run of the live model chose the flat form. A real provider is not
        going to be this forgiving; normalising the model's dotted keys back
        into nested objects before the call is the fix, and it belongs in
        `resource_attributes.build_resource_attributes`.
        """

        attributes = body["message"]["contract"]["commitments"][0]["resources"][0][
            "resourceAttributes"
        ]
        nested = attributes.get("commodity")
        if isinstance(nested, dict) and "code" in nested:
            return nested["code"]
        flat = attributes.get("commodity.code")
        return flat if isinstance(flat, str) else None


@pytest.fixture
def live_network(httpserver: HTTPServer) -> _Network:
    network = _Network()
    httpserver.expect_request("/discover").respond_with_handler(network.discover)
    httpserver.expect_request("/select").respond_with_handler(network.select)
    return network


@pytest.fixture
def live_app(httpserver: HTTPServer, live_network: _Network, tmp_path: Path):
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


def test_a_live_model_answers_the_onion_price(live_app, live_network, a_body) -> None:
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
            },
        ),
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    message = response.json()["message"]

    # Moderation let an ordinary price question through. A refusal would have
    # stopped the turn before discovery.
    assert message["outcome"]["status"] == "answered", message["outcome"]

    # Intent, read off the wire: only `Market` + `observe` resolves to this
    # capability in the schema-pack index, so the filter naming it proves the
    # classification.
    assert live_network.discover_requests, "discovery never ran"
    expression = live_network.discover_requests[0]["message"]["intent"]["filters"][
        "expression"
    ]
    assert CAPABILITY in expression, expression

    # The planner read "onion" out of the sentence and put it in a field the
    # pack declares filterable. The server would have answered 400 otherwise.
    # Read through `_commodity_code`, which tolerates both shapes the DSS may
    # send — see its docstring; that tolerance is covering a real defect.
    assert live_network.select_requests, "the provider was never called"
    commodity = _Network._commodity_code(live_network.select_requests[0])
    assert commodity and "onion" in commodity.lower(), commodity

    # `/select` went to the provider `/discover` offered, not one the model
    # invented.
    offer = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "offer"
    ]
    assert offer["provider"]["id"] == PROVIDER_ID

    # The composer wrote the provider's number, not its own. Digits only —
    # wording, currency placement and thousands separators are the model's
    # choice and asserting them would make this a coin flip.
    text = " ".join(block.get("text", "") for block in message["content"])
    # Printed because tier 6 is diagnostic: `-s` shows what the model wrote, so
    # a drift in tone or length is visible without adding an assertion that
    # would make the test a coin flip.
    print(f"\n  model wrote: {text!r}")
    digits = re.sub(r"[,\s]", "", text)
    assert str(_SENTINEL_MODAL) in digits, text
