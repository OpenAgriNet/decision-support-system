"""Transport edges. Each one is a request a caller can actually send.

The rule under test throughout: a request the DSS cannot process is a 4xx, and a
turn the DSS processed is a 200 whatever the outcome. An unhandled exception is
neither, so a 500 here is always a defect.
"""

from __future__ import annotations

import gzip
import json

from fastapi.testclient import TestClient
from tests.support.fakes import FakeRunner

from dss.config.settings import Settings
from dss.core.shared.models import (
    Claim,
    TextBlock,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)
from dss.entrypoint.app import build_app

JSON = "application/json"

ANSWER = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
    content=(TextBlock(text="Rs 2,275 per quintal."),),
)


def _client(runner=None, **overrides):
    runner = runner or FakeRunner(
        [TurnStarted(), Claim(content=ANSWER.content[0]), ANSWER]
    )
    return TestClient(
        build_app(runner=runner, settings=Settings(**overrides)),
        raise_server_exceptions=False,
    )


def test_a_body_that_is_not_gzip_but_claims_to_be_is_a_bad_request(a_body):
    """`Content-Encoding: gzip` on plain JSON. A caller misconfiguring their
    client must not take the server down."""

    response = _client().post(
        "/v1/turns",
        content=json.dumps(a_body()).encode(),
        headers={"Content-Type": JSON, "Content-Encoding": "gzip"},
    )

    assert response.status_code == 400


def test_truncated_gzip_is_a_bad_request(a_body):
    packed = gzip.compress(json.dumps(a_body()).encode())

    response = _client().post(
        "/v1/turns",
        content=packed[: len(packed) // 2],
        headers={"Content-Type": JSON, "Content-Encoding": "gzip"},
    )

    assert response.status_code == 400


def test_a_thread_of_only_assistant_messages_is_unprocessable(a_body):
    """The schema accepts it — `input` requires one entry and an assistant
    message is one. There is still no question to answer, so it is a 422."""

    body = a_body(
        message__input=[
            {"role": "assistant", "content": [{"type": "text", "text": "Hello?"}]}
        ]
    )

    response = _client().post("/v1/turns", json=body)

    assert response.status_code == 422


def test_an_unknown_content_encoding_is_passed_through_untouched(a_body):
    """Only gzip is decoded. Anything else is left alone rather than guessed at,
    so a body that is really JSON still works."""

    response = _client().post(
        "/v1/turns",
        content=json.dumps(a_body()).encode(),
        headers={"Content-Type": JSON, "Content-Encoding": "identity", "Accept": JSON},
    )

    assert response.status_code == 200


def test_a_runner_that_yields_nothing_still_produces_a_terminal_body(a_body):
    """A runner is not supposed to finish without a terminal event. If one does,
    the caller gets an answer shaped like every other failure rather than an
    empty 200."""

    response = _client(FakeRunner([])).post(
        "/v1/turns", json=a_body(), headers={"Accept": JSON}
    )

    assert response.status_code == 200
    assert response.json()["message"]["outcome"]["status"] == "unavailable"


def test_a_charset_on_the_content_type_is_accepted(a_body):
    """`application/json; charset=utf-8` is what several HTTP clients send by
    default. Rejecting it would fail real callers over a parameter."""

    response = _client().post(
        "/v1/turns",
        content=json.dumps(a_body()).encode(),
        headers={"Content-Type": "application/json; charset=utf-8", "Accept": JSON},
    )

    assert response.status_code == 200


def test_a_body_that_is_valid_json_but_not_an_object_is_unprocessable():
    response = _client().post(
        "/v1/turns", content=b"[1, 2, 3]", headers={"Content-Type": JSON}
    )

    assert response.status_code == 422


def test_a_body_that_is_not_utf8_is_a_bad_request():
    response = _client().post(
        "/v1/turns", content=b"\xff\xfe{}", headers={"Content-Type": JSON}
    )

    assert response.status_code == 400


def test_a_decompression_bomb_is_refused(a_body):
    """A few KB of gzip that inflates far past the cap. The cap is enforced as
    the body is produced, so the payload never lands in memory."""

    app = _client(max_body_bytes=10_000)
    bomb = gzip.compress(b"\x00" * 20_000_000)

    response = app.post(
        "/v1/turns",
        content=bomb,
        headers={"Content-Type": JSON, "Content-Encoding": "gzip"},
    )

    assert response.status_code == 413
    assert len(bomb) < 100_000, "the compressed payload really is small"


def test_a_declared_length_over_the_cap_is_refused(a_body):
    """`Content-Length` is consulted first, so an oversized body is rejected
    before it is read."""

    app = _client(max_body_bytes=100)

    response = app.post("/v1/turns", json=a_body(), headers={"Content-Type": JSON})

    assert response.status_code == 413
