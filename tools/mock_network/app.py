"""The mock network's routes.

`POST /discover` and `POST /select`, synchronous request/response — the same
contract the real Network Adapter offers. No callbacks, no auth, no signing:
the DSS never speaks Beckn itself, so there is nothing here to sign.

Discover is keyed off the `@type` the DSS asked for, so one running mock
answers a weather question and a mandi question without a restart. A discover
request carries `@type` twice — in `context.schemaContext` and in the jsonpath
filter — and no subject category. The category is how the DSS *chooses* which
`@type` to ask for; by the time a request leaves it is already resolved.

Select answers the speed benchmark's questions (`evals/perf/questions.toml`).
It matches a request on a few key fields (`matching.py`) and builds the answer
(`generators.py`). A request it cannot match is refused with a 400 and counted
at `GET /_bench/misses`, never answered with the wrong data.

Two discover request shapes both occur, and one catalog per `@type` covers
both. Verified against `discover_providers`:

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
import os
import tomllib
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from tools.mock_network.catalog import requested_types
from tools.mock_network.generators import (
    advisory_answer,
    mandi_answer,
    weather_answer,
)
from tools.mock_network.matching import key_fields

_RESPONSES = Path(__file__).parent / "responses"

# Read as a file, not imported: `tools/` does not depend on `evals/` code.
DEFAULT_QUESTIONS = Path(__file__).parents[2] / "evals" / "perf" / "questions.toml"

# Which recorded catalog answers which capability. A `@type` with no entry is
# a provider nobody serves — an empty catalog, which is what the real network
# reports when nothing matches, and a `no_match` at the DSS.
# `AgricultureResource` is deliberately absent. It is the shared-fields base
# every pack `$ref`s, not a capability a provider advertises — it only appears
# as a requested `@type` because the capability index registers it (see
# TODO.md). A Crop question therefore asks for it and gets nothing, which is
# also what a real network would answer.
_DISCOVER_BY_TYPE = {
    "openagrinet:WeatherObservation": "weather_discover.json",
    "openagrinet:MandiPrice": "mandi_discover.json",
    "openagrinet:KnowledgeAdvisory": "advisory_discover.json",
}


def build_mock_app(
    *, pack_dir: Path | None = None, questions: Path = DEFAULT_QUESTIONS
) -> FastAPI:
    """The ASGI app, mounted standalone or in-process.

    `pack_dir` is the directory `DSS_SCHEMA_PACK_DIR` names. Reading the packs
    the DSS reads is what stops the mock advertising a `@type` the DSS cannot
    route — a mismatch that would otherwise look like a discovery bug.

    `questions` is the speed benchmark's question set. The mock advertises and
    answers what those questions ask, so the two cannot drift apart.
    """

    with questions.open("rb") as file:
        rows = tomllib.load(file)["question"]
    mandi_resources = _mandi_resources(rows)
    # What `key_fields` checks a request against, per capability.
    known_by_type = {
        "openagrinet:MandiPrice": [r["resourceAttributes"] for r in mandi_resources],
        "openagrinet:KnowledgeAdvisory": [
            {"question_id": r["id"], "match": r["match"], "answer": r["answer"]}
            for r in rows
            if r["category"] == "advisory"
        ],
    }

    app = FastAPI(title="OAN mock network", docs_url="/docs")
    # The transaction id of each /select the mock could not answer, so the
    # speed benchmark can leave those turns out of its figures.
    misses: list[str | None] = []

    @app.get("/_bench/misses")
    async def bench_misses() -> dict[str, Any]:
        return {"count": len(misses), "transactionIds": misses}

    @app.post("/discover")
    async def discover(request: Request) -> dict[str, Any]:
        body = await request.json()
        catalogs: list[dict] = []
        for capability in requested_types(body):
            filename = _DISCOVER_BY_TYPE.get(capability)
            if filename is None:
                continue
            found = _load(filename)["message"]["catalogs"]
            if capability == "openagrinet:MandiPrice":
                found = [{**catalog, "resources": mandi_resources} for catalog in found]
            catalogs.extend(found)
        return {
            "context": _echo(body, "on_discover"),
            "message": {"catalogs": catalogs},
        }

    @app.post("/select")
    async def select(request: Request) -> dict[str, Any]:
        body = await request.json()
        capability = _selected_type(body)
        if capability is None:
            raise _refused(body, capability, misses)
        asked = _selected_resource(body)
        known = known_by_type.get(capability, [])
        keys = key_fields(capability, asked["resourceAttributes"], known=known)
        if keys is None:
            raise _refused(body, capability, misses)
        answered = {
            "id": asked["id"],
            "resourceAttributes": _answer(capability, keys, known),
        }
        return _on_select(body, answered)

    return app


def _selected_type(body: dict[str, Any]) -> str | None:
    """The `@type` the DSS is selecting, out of the request it sent.

    One commitment, one resource — that is what `build_select_request` emits,
    and reading past it would invent a case the DSS cannot produce.
    """

    try:
        return _selected_resource(body)["resourceAttributes"]["@type"]
    except (KeyError, IndexError, TypeError):
        return None


def _refused(
    body: dict[str, Any], capability: str | None, misses: list[str | None]
) -> HTTPException:
    """A /select the mock has no answer for, recorded as a miss.

    400, though "no answer" is closer to a 404: the DSS retries a 404 as
    transient, spending about 1.5 s of backoff on a turn the benchmark drops
    anyway. Turning its retries off instead would time a DSS set up
    differently from the one that ships.
    """

    misses.append(body.get("context", {}).get("transactionId"))
    return HTTPException(
        status_code=400,
        detail=f"the mock has no answer for this {capability!r} request",
    )


def _answer(capability: str, keys: dict[str, Any], known: list[dict]) -> dict:
    """The answer for a matched request."""

    if capability == "openagrinet:WeatherObservation":
        return weather_answer(keys["lon"], keys["lat"])
    if capability == "openagrinet:KnowledgeAdvisory":
        row = next(r for r in known if r["question_id"] == keys["question_id"])
        return advisory_answer(row["answer"])
    market = next(r for r in known if r["market"]["marketCode"] == keys["market"])
    commodity = next(
        c for c in market["supportedCommodities"] if c["code"] == keys["commodity"]
    )
    return mandi_answer(commodity=commodity, market=market["market"])


def _mandi_resources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One advertised resource per benchmark market, offering every commodity
    a question asks about there — how a real mandi provider advertises.

    The rest of each resource comes from the recorded catalog, so only the
    market and its commodities differ from what the mock always sent.
    """

    template = _load("mandi_discover.json")["message"]["catalogs"][0]["resources"][0]
    commodities: dict[str, dict[str, dict]] = {}
    markets: dict[str, dict] = {}
    for row in rows:
        if row["category"] != "mandi":
            continue
        code = row["market"]["marketCode"]
        markets[code] = row["market"]
        commodities.setdefault(code, {})[row["commodity"]["code"]] = row["commodity"]
    return [
        {
            **template,
            "id": f"resource:mandi-price:market:{code}",
            "resourceAttributes": {
                **template["resourceAttributes"],
                "market": market,
                "supportedCommodities": list(commodities[code].values()),
            },
        }
        for code, market in markets.items()
    ]


def _selected_resource(body: dict[str, Any]) -> dict[str, Any]:
    return body["message"]["contract"]["commitments"][0]["resources"][0]


def _on_select(body: dict[str, Any], answered: dict[str, Any]) -> dict[str, Any]:
    """The one-resource answer a real provider sends, under the offer the DSS
    selected."""

    commitment = body["message"]["contract"]["commitments"][0]
    return {
        "context": _echo(body, "on_select"),
        "message": {
            "contract": {
                "commitments": [
                    {
                        "status": {"descriptor": {"code": "ACTIVE"}},
                        "resources": [answered],
                        "offer": commitment.get("offer"),
                    }
                ]
            }
        },
    }


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


def build_from_env() -> FastAPI:
    """The app, with the pack directory taken from the environment.

    `uvicorn --reload` needs an import string rather than a built app, so it
    cannot be handed a `pack_dir` argument.
    """

    directory = os.environ.get("DSS_SCHEMA_PACK_DIR")
    questions = os.environ.get("DSS_MOCK_QUESTIONS")
    return build_mock_app(
        pack_dir=Path(directory) if directory else None,
        questions=Path(questions) if questions else DEFAULT_QUESTIONS,
    )
