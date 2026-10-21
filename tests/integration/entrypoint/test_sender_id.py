"""The request context's `senderId` field, through a real ASGI client
(ADR-0020).

Nothing below the port is wired: these are statements about the transport —
that the field reaches the turn, and that a caller is never refused for the
state of it. What the id then *does* is tested in
`tests/unit/core/provider_preferences/`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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


def _post(body: dict) -> FakeRunner:
    runner = FakeRunner(EVENTS)
    client = TestClient(build_app(runner=runner, settings=Settings()))

    response = client.post("/v1/turns", json=body)

    assert response.status_code == 200
    return runner


def test_the_sender_id_reaches_the_turn(a_body):
    runner = _post(a_body(context__senderId="kisan-mitra-web"))

    turn, _ctx = runner.calls[0]
    assert turn.sender_id == "kisan-mitra-web"


def test_a_request_without_a_sender_id_is_answered_all_the_same(a_body):
    """Rolling the field out must be a configuration step for an experience
    layer, never a breaking change for its farmers."""

    runner = _post(a_body())

    turn, _ctx = runner.calls[0]
    assert turn.sender_id is None


def test_a_blank_sender_id_counts_as_no_sender(a_body):
    """A relay that fills the field with an empty string has not named
    anybody, and `""` must not be looked up as if it were a sender."""

    runner = _post(a_body(context__senderId="   "))

    turn, _ctx = runner.calls[0]
    assert turn.sender_id is None


def test_an_unrecognised_sender_is_answered_rather_than_refused(a_body):
    """The transport does not know which senders are registered, and must
    not: an unknown one is served the default ranking further in."""

    runner = _post(a_body(context__senderId="never-heard-of-this-one"))

    turn, _ctx = runner.calls[0]
    assert turn.sender_id == "never-heard-of-this-one"
