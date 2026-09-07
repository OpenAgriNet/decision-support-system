"""The HTTP layer, driven through a real ASGI client against a fake runner.

Nothing below the port is wired: no core, no LLM. That is the point — every case
here is a statement about the transport alone.
"""

from __future__ import annotations

import gzip
import json

import pytest
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

SSE = "text/event-stream"
JSON = "application/json"

ANSWER = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
    content=(TextBlock(text="Rs 2,275 per quintal.", source_ids=()),),
)
EVENTS = [TurnStarted(), Claim(content=ANSWER.content[0]), ANSWER]


def client(runner=None, **settings_overrides) -> tuple[TestClient, FakeRunner]:
    runner = runner or FakeRunner(EVENTS)
    settings = Settings(**settings_overrides)
    return TestClient(build_app(runner=runner, settings=settings)), runner


def frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        name = lines[0].removeprefix("event: ")
        out.append((name, json.loads(lines[1].removeprefix("data: "))))
    return out


# --- happy path -------------------------------------------------------------


def test_a_streaming_turn_returns_the_event_sequence(a_body):
    app, _ = client()

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": SSE})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(SSE)
    assert [name for name, _ in frames(response.text)] == [
        "turn.created",
        "claim.completed",
        "turn.completed",
    ]
    assert [f["context"]["sequenceNumber"] for _, f in frames(response.text)] == [
        1,
        2,
        3,
    ]


def test_a_json_turn_returns_the_terminal_body_alone(a_body):
    app, _ = client()

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    assert response.status_code == 200
    body = response.json()
    assert body["message"]["outcome"]["status"] == "answered"
    assert "sequenceNumber" not in body["context"]


def test_both_modes_describe_the_same_turn(a_body):
    streaming, _ = client()
    plain, _ = client()

    stream = streaming.post("/v1/turns", json=a_body(), headers={"Accept": SSE})
    single = plain.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    terminal = frames(stream.text)[-1][1]
    assert terminal["message"] == single.json()["message"]


# --- request rules ----------------------------------------------------------


def test_a_malformed_body_is_a_bad_request():
    app, runner = client()

    response = app.post(
        "/v1/turns", content=b"{not json", headers={"Content-Type": JSON}
    )

    assert response.status_code == 400
    assert runner.calls == []


def test_an_empty_input_list_is_unprocessable(a_body):
    app, runner = client()

    response = app.post("/v1/turns", json=a_body(message__input=[]))

    assert response.status_code == 422
    assert runner.calls == []


def test_an_unknown_field_is_unprocessable(a_body):
    app, _ = client()
    body = a_body()
    body["message"]["attributes"]["temperature"] = 0.7

    assert app.post("/v1/turns", json=body).status_code == 422


def test_a_non_json_content_type_is_unsupported(a_body):
    app, _ = client()

    response = app.post(
        "/v1/turns",
        content=json.dumps(a_body()),
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 415


def test_a_body_over_the_cap_is_rejected(a_body):
    """Checked after decompression: a small gzip payload can expand past the
    cap, so the compressed length proves nothing."""

    app, _ = client(max_body_bytes=200)
    packed = gzip.compress(json.dumps(a_body()).encode())

    response = app.post(
        "/v1/turns",
        content=packed,
        headers={"Content-Type": JSON, "Content-Encoding": "gzip"},
    )

    assert response.status_code == 413


def test_a_gzipped_body_within_the_cap_is_accepted(a_body):
    app, _ = client()
    packed = gzip.compress(json.dumps(a_body()).encode())

    response = app.post(
        "/v1/turns",
        content=packed,
        headers={"Content-Type": JSON, "Content-Encoding": "gzip", "Accept": JSON},
    )

    assert response.status_code == 200


# --- content negotiation ----------------------------------------------------


@pytest.mark.parametrize("accept", [JSON, "*/*", None])
def test_anything_but_the_event_stream_gets_json(a_body, accept):
    app, _ = client()
    headers = {} if accept is None else {"Accept": accept}

    response = app.post("/v1/turns", json=a_body(), headers=headers)

    assert response.headers["content-type"].startswith(JSON)


def test_an_unsatisfiable_accept_is_refused(a_body):
    app, _ = client()

    response = app.post(
        "/v1/turns", json=a_body(), headers={"Accept": "application/xml"}
    )

    assert response.status_code == 406


# --- capacity and readiness -------------------------------------------------


def test_a_saturated_dss_says_come_back_later(a_body):
    app, _ = client(max_concurrent_turns=0)

    response = app.post("/v1/turns", json=a_body())

    assert response.status_code == 429
    assert response.headers["retry-after"]


def test_an_unready_dss_is_unavailable(a_body):
    app, _ = client(ready=False)

    assert app.post("/v1/turns", json=a_body()).status_code == 503


# --- trace context ----------------------------------------------------------


def test_the_trace_id_echoes_the_callers_transaction_id(a_body):
    """The contract's whole correlation story: the caller sends
    `transactionId`, the DSS echoes it as `traceId`."""

    app, _ = client()
    body = a_body()
    body["context"]["transactionId"] = "txn_correlate_me"

    response = app.post("/v1/turns", json=body, headers={"Accept": JSON})

    assert response.json()["context"]["traceId"] == "txn_correlate_me"


def test_a_response_always_carries_a_message_id(a_body):
    """Required by the contract, so the DSS mints one when the caller omits it."""

    app, _ = client()
    body = a_body()
    body["context"].pop("messageId", None)

    response = app.post("/v1/turns", json=body, headers={"Accept": JSON})

    assert response.json()["context"]["messageId"]


# --- failures ---------------------------------------------------------------


def test_a_fault_mid_stream_keeps_the_frames_already_sent(a_body):
    """HTTP was 200 before anyone knew, so it stays 200. The terminal event is
    where the failure is reported."""

    app, _ = client(FakeRunner(EVENTS, fail_after=2))

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": SSE})

    assert response.status_code == 200
    sent = frames(response.text)
    assert [name for name, _ in sent] == [
        "turn.created",
        "claim.completed",
        "turn.failed",
    ]
    assert sent[-1][1]["message"]["outcome"]["cause"] == "internal"


def test_a_fault_in_json_mode_is_still_a_two_hundred(a_body):
    """Not a 502: a dependency failure always lands in the outcome."""

    app, _ = client(FakeRunner(EVENTS, fail_after=0))

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    assert response.status_code == 200
    assert response.json()["message"]["outcome"]["status"] == "unavailable"
