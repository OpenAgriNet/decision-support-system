"""The `X-Client-Id` header, through a real ASGI client (ADR-0019).

Nothing below the port is wired: these are statements about the transport —
that the header reaches the turn, and that a caller is never refused for the
state of it. What the id then *does* is tested in
`tests/unit/core/provider_preferences/`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from dss.adapters.http.v1.router import CLIENT_ID_HEADER
from dss.config.settings import Settings
from dss.core.shared.models import (
    TextBlock,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)
from dss.entrypoint.app import build_app
from tests.support.fakes import FakeRunner

EVENTS = [
    TurnStarted(),
    TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text="Rs 2,275 per quintal.", source_ids=()),),
    ),
]


def _post(a_body, headers: dict[str, str] | None = None) -> FakeRunner:
    runner = FakeRunner(EVENTS)
    client = TestClient(build_app(runner=runner, settings=Settings()))

    response = client.post("/v1/turns", json=a_body(), headers=headers)

    assert response.status_code == 200
    return runner


def test_the_header_reaches_the_turn(a_body):
    runner = _post(a_body, {CLIENT_ID_HEADER: "kisan-mitra-web"})

    turn, _ctx = runner.calls[0]
    assert turn.client_id == "kisan-mitra-web"


def test_a_request_without_the_header_is_answered_all_the_same(a_body):
    """Rolling the header out must be a configuration step for an experience
    layer, never a breaking change for its farmers."""

    runner = _post(a_body)

    turn, _ctx = runner.calls[0]
    assert turn.client_id is None


def test_a_blank_header_counts_as_no_client(a_body):
    """A proxy that sets the header to an empty string has not named anybody,
    and `""` must not be looked up as if it were a client."""

    runner = _post(a_body, {CLIENT_ID_HEADER: "   "})

    turn, _ctx = runner.calls[0]
    assert turn.client_id is None


def test_an_unrecognised_client_is_answered_rather_than_refused(a_body):
    """The transport does not know which clients are configured, and must
    not: an unknown one is served the default ranking further in."""

    runner = _post(a_body, {CLIENT_ID_HEADER: "never-heard-of-this-one"})

    turn, _ctx = runner.calls[0]
    assert turn.client_id == "never-heard-of-this-one"


def test_the_header_is_published_on_the_contract(a_body):
    client = TestClient(build_app(runner=FakeRunner(EVENTS), settings=Settings()))

    schema = client.get("/openapi.json").json()
    parameters = schema["paths"]["/v1/turns"]["post"]["parameters"]

    assert [p["name"] for p in parameters] == [CLIENT_ID_HEADER]
    assert parameters[0]["required"] is False
