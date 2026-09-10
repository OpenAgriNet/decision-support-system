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
- `/select` carrying the commodity *code* `23` can only happen if the planner
  read "onion" out of the farmer's sentence, matched it to `23=Onion` among the
  three entries `describe_capability` advertised, and sent the provider's own
  identifier rather than the English word. Two decoys make a correct match mean
  something.

There are two tests: the same turn over `Accept: application/json` and over
`Accept: text/event-stream`. Each is a live turn, so running the file costs two
sets of model calls.

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
# deliberately supplied a key — a sourced `.env`, or an IDE run configuration —
# which is exactly when you want it to.
pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="live model test — set OPENAI_API_KEY and OPENAI_BASE_URL",
)

MODEL = "azure:gpt-5.6-luna"
QUERY = "what is the price of onion"

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

# The vocabulary the fixture advertises, and the code onion sits under.
#
# The planner is told to match the farmer's word to a *name* in this list and
# send that entry's *code* — never a code it recalls from elsewhere
# (`config/defaults/skills/provider-invocation.md`). So `23` reaching `/select`
# is the assertion: the model read "onion", found `23=Onion` among three
# entries, and sent the provider's own identifier rather than the English word.
#
# Wheat and Tomato are decoys. With one entry, sending it would prove nothing.
_ADVERTISED = _RESOURCE["resourceAttributes"]["supportedCommodities"]
ONION_CODE = next(
    entry["code"] for entry in _ADVERTISED if entry["name"].lower() == "onion"
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
    """Read the commodity from either shape the DSS may send.

    Tolerant on purpose, and it should not have to be. A pack's
    `filterable_paths` are dotted JSON paths
    (`beckn:resourceAttributes.commodity.code`) describing a *nested* document,
    but `describe_capability` shows the model those dotted strings and nothing
    converts what comes back. `validate_arguments` accepts both, because
    `_flatten` reduces the nested form to the same dotted path — so the DSS
    emits `{"commodity": {"code": ...}}` or `{"commodity.code": ...}` depending
    on which the model picked, and a provider expecting the Beckn shape would
    only understand one.

    Live runs have produced both. A real provider is not going to be this
    forgiving; normalising the model's dotted keys back into nested objects
    before the call is the fix, and it belongs in
    `resource_attributes.build_resource_attributes`.
    """

    attributes = select_body["message"]["contract"]["commitments"][0]["resources"][0][
        "resourceAttributes"
    ]
    nested = attributes.get("commodity")
    if isinstance(nested, dict) and "code" in nested:
        return nested["code"]
    flat = attributes.get("commodity.code")
    return flat if isinstance(flat, str) else None


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
        expression = body["message"]["intent"]["filters"]["expression"]
        if CAPABILITY not in expression:
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
                    "error": (
                        f"this provider serves commodity {ONION_CODE!r}, "
                        f"not {commodity!r}"
                    )
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


def _turn_body(a_body, query: str) -> dict[str, Any]:
    return a_body(
        message__input=[{"role": "user", "content": [{"type": "text", "text": query}]}],
        message__attributes={
            "sourceLanguage": "en",
            "targetLanguage": "en",
            "channel": "web",
        },
    )


def _ask(client: TestClient, a_body, query: str):
    return client.post(
        "/v1/turns",
        json=_turn_body(a_body, query),
        headers={"Accept": "application/json"},
    )


def _frames(stream: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into ``(event name, decoded data)`` pairs.

    Splitting on a blank line is safe because the payload is JSON: a newline
    inside the model's prose is escaped to ``\\n`` and cannot end a frame. That
    is one of the things this test exists to confirm — see its assertions.
    """

    parsed = []
    for block in stream.strip().split("\n\n"):
        lines = block.split("\n")
        parsed.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return parsed


def test_a_live_model_answers_the_onion_price(live_app, live_network, a_body) -> None:
    response = _ask(TestClient(live_app), a_body, QUERY)

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
    assert live_network.select_requests, "the provider was never called"
    commodity = _commodity_code(live_network.select_requests[0])
    assert commodity == ONION_CODE, (
        f"expected the advertised code {ONION_CODE!r} for Onion, got {commodity!r} — "
        f"the model must match the farmer's word to a name in {_ADVERTISED} "
        "and send that entry's code"
    )

    # `/select` went to the provider `/discover` offered, not one the model
    # invented.
    offer = live_network.select_requests[0]["message"]["contract"]["commitments"][0][
        "offer"
    ]
    assert offer["provider"]["id"] == PROVIDER_ID

    # The composer wrote the provider's number, not its own. Digits only —
    # wording, currency placement and thousands separators are the model's
    # choice, and asserting them would make this a coin flip.
    text = " ".join(block.get("text", "") for block in message["content"])
    # Printed so `-s` shows what the model wrote: a drift in tone or length
    # stays visible without an assertion that would be a coin flip.
    print(f"\n  model wrote: {text!r}")
    assert str(EXPECTED_MIN) in re.sub(r"[,\s]", "", text), text
    assert str(EXPECTED_MAX) in re.sub(r"[,\s]", "", text), text


def test_the_same_turn_streams_as_sse(live_app, live_network, a_body) -> None:
    """The same turn over `Accept: text/event-stream`.

    Tier 3 already pins the frame names and the sequence numbers
    (`test_turn_route.py`), but against a fake runner whose claim carries canned
    text. What only this tier can show is a *model's* prose surviving the
    framing: the answer holds a currency symbol and may hold newlines, and an
    SSE frame is delimited by a blank line — so an unescaped payload would split
    one frame into two and the stream would not parse at all.
    """

    with TestClient(live_app) as client:
        with client.stream(
            "POST",
            "/v1/turns",
            json=_turn_body(a_body, QUERY),
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            raw = "".join(response.iter_text())

    # Parsing at all is the first assertion: `_frames` splits on a blank line,
    # so a raw newline in the prose would surface here as a JSON error.
    events = _frames(raw)
    print(f"\n  frames: {[name for name, _ in events]}")

    assert [name for name, _ in events] == [
        "turn.created",
        "claim.completed",
        "turn.completed",
    ]
    # `turn.completed`, not `turn.failed` — the terminal name is chosen from the
    # real outcome, and only UNAVAILABLE is a failed turn (`sse._terminal_name`).
    assert events[-1][1]["message"]["outcome"]["status"] == "answered"

    # One counter per turn, starting at 1 (the contract's minimum).
    assert [frame["context"]["sequenceNumber"] for _, frame in events] == [1, 2, 3]

    # Every frame files the turn under the same ids, so a client can join them.
    trace_ids = {frame["context"]["traceId"] for _, frame in events}
    assert len(trace_ids) == 1, trace_ids

    # The claim frame carries what the model actually wrote, intact through
    # JSON encoding and SSE framing.
    claim = next(frame for name, frame in events if name == "claim.completed")
    text = " ".join(block.get("text", "") for block in claim["message"]["content"])
    print(f"  streamed: {text!r}")
    assert str(EXPECTED_MIN) in re.sub(r"[,\s]", "", text), text
    assert str(EXPECTED_MAX) in re.sub(r"[,\s]", "", text), text

    # A non-ASCII character round-tripped rather than being mangled or escaped
    # into a literal `\uXXXX` the client would have to decode itself.
    assert text == text.encode("utf-8").decode("utf-8")

    # The terminal frame carries the sources, and every citation in the claim
    # resolves against them — the same referential check the JSON test makes,
    # but across two frames rather than one body.
    listed = {source["id"] for source in events[-1][1]["message"]["sources"]}
    cited = {
        annotation["sourceId"]
        for block in claim["message"]["content"]
        for annotation in block.get("annotations", ())
    }
    assert cited, "the streamed answer cited nothing"
    assert cited <= listed, f"cited {cited - listed}, listed {listed}"
