"""The mock network's routes.

`POST /discover` and `POST /select`, synchronous request/response — the same
contract the real Network Adapter offers. No callbacks, no auth, no signing:
the DSS never speaks Beckn itself, so there is nothing here to sign.

Scenarios are keyed off the `@type` the DSS asked for, so one running mock
answers a weather question and a mandi question without a restart. That is the
whole routing key: a discover request carries `@type` twice — in
`context.schemaContext` and in the jsonpath filter — and no subject category.
The category is how the DSS *chooses* which `@type` to ask for; by the time a
request leaves it is already resolved.

Two request shapes both occur, and one file per `@type` covers both. Verified
against `discover_providers`:

- **Two categories** ("mandi price and the weather") are two asks, so the DSS
  sends *two* concurrent requests, one `@type` each. Each arrives here
  separately.
- **One category resolving to two capabilities** — `('Crop', 'Knowledge')`
  maps to both `AgricultureResource` and `KnowledgeAdvisory` — is one request
  naming both. Every match is returned: a real network answers one query with
  every matching catalog, and returning only the first would make the turn
  partially answered for no reason a log would explain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

from tools.mock_network.catalog import requested_types

_RESPONSES = Path(__file__).parent / "responses"

# Which recorded catalog answers which capability. A `@type` with no entry is
# a provider nobody serves — an empty catalog, which is what the real network
# reports when nothing matches, and a `no_match` at the DSS.
_DISCOVER_BY_TYPE = {
    "openagrinet:WeatherObservation": "weather_discover.json",
}


def build_mock_app(*, pack_dir: Path | None = None) -> FastAPI:
    """The ASGI app, mounted standalone or in-process.

    `pack_dir` is the directory `DSS_SCHEMA_PACK_DIR` names. Reading the packs
    the DSS reads is what stops the mock advertising a `@type` the DSS cannot
    route — a mismatch that would otherwise look like a discovery bug.
    """

    app = FastAPI(title="OAN mock network", docs_url="/docs")

    @app.post("/discover")
    async def discover(request: Request) -> dict[str, Any]:
        body = await request.json()
        catalogs: list[dict] = []
        for capability in requested_types(body):
            filename = _DISCOVER_BY_TYPE.get(capability)
            if filename is not None:
                catalogs.extend(_load(filename)["message"]["catalogs"])
        return {
            "context": _echo(body, "on_discover"),
            "message": {"catalogs": catalogs},
        }

    return app


def _load(filename: str) -> dict[str, Any]:
    return json.loads((_RESPONSES / filename).read_text(encoding="utf-8"))


def _echo(body: dict[str, Any], action: str) -> dict[str, Any]:
    """A plausible response context.

    The DSS reads only `message`, so this is for whoever is watching the wire.
    The correlation ids are echoed rather than invented, because a mismatched
    pair would mislead anyone comparing request and response by hand.
    """

    sent = body.get("context", {})
    return {
        "action": action,
        "version": sent.get("version", "2.0.0"),
        "networkId": "oan-mock",
        "transactionId": sent.get("transactionId"),
        "messageId": sent.get("messageId"),
        "timestamp": sent.get("timestamp"),
    }
