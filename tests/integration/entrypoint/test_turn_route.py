"""The HTTP layer, driven through a real ASGI client against a fake runner.

Nothing below the port is wired: no core, no LLM. That is the point — every case
here is a statement about the transport alone.
"""

from __future__ import annotations

import gzip
import json

import pytest
from fastapi.testclient import TestClient

from dss.config.settings import Settings
from dss.core.forms.models import (
    FieldDefinition,
    FieldKind,
    FieldRules,
    FormBlock,
    FormContext,
    FormDefinition,
)
from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    TextBlock,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)
from dss.entrypoint.app import build_app
from tests.support.fakes import FakeRunner

SSE = "text/event-stream"
JSON = "application/json"

ANSWER = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
    content=(TextBlock(text="Rs 2,275 per quintal.", source_ids=()),),
)
# What the runner actually produces: the composer's pieces, then the finished
# block, then the terminal event.
DELTAS = ("Rs 2,2", "75 per quin", "tal.")
EVENTS = [
    TurnStarted(),
    *[ClaimDelta(text=text) for text in DELTAS],
    Claim(content=ANSWER.content[0]),
    ANSWER,
]

GRIEVANCE_FORM = FormBlock(
    form_id="8f3c",
    form=FormDefinition(
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        fields=(
            FieldDefinition(
                name="applicantMobile",
                label="Applicant mobile",
                kind=FieldKind.TEXT,
                rules=FieldRules(required=True),
            ),
        ),
    ),
)
REFERRAL = TextBlock(text="Please fill in the form below.", source_ids=())
FORM_OFFERED = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.REQUIRES_INPUT, confidence=80),
    content=(REFERRAL, GRIEVANCE_FORM),
)
FORM_EVENTS = [
    TurnStarted(),
    ClaimDelta(text="Please fill in "),
    ClaimDelta(text="the form below."),
    Claim(content=REFERRAL),
    Claim(content=GRIEVANCE_FORM),
    FORM_OFFERED,
]


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


def test_a_streaming_turn_returns_the_event_sequence(a_body):
    app, _ = client()

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": SSE})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(SSE)
    assert [name for name, _ in frames(response.text)] == [
        "turn.created",
        "claim.delta",
        "claim.delta",
        "claim.delta",
        "claim.completed",
        "turn.completed",
    ]
    assert [f["context"]["sequenceNumber"] for _, f in frames(response.text)] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]


def test_the_delta_frames_join_back_to_the_completed_claim(a_body):
    """The guarantee a consumer renders on: concatenating the pieces gives the
    text the finished claim carries, character for character."""

    app, _ = client()

    sent = frames(app.post("/v1/turns", json=a_body(), headers={"Accept": SSE}).text)

    deltas = [f for name, f in sent if name == "claim.delta"]
    completed = next(f for name, f in sent if name == "claim.completed")
    joined = "".join(d["message"]["content"][0]["text"] for d in deltas)
    assert joined == completed["message"]["content"][0]["text"]


def test_a_delta_carries_no_citations(a_body):
    """Mid-write the block has no end, so there is nothing to cite over. The
    annotations arrive with the completed claim."""

    app, _ = client()

    sent = frames(app.post("/v1/turns", json=a_body(), headers={"Accept": SSE}).text)

    delta = next(f for name, f in sent if name == "claim.delta")
    block = delta["message"]["content"][0]
    assert block["type"] == "output_text_delta"
    assert "annotations" not in block
    assert delta["message"].get("outcome") is None


def test_a_json_turn_returns_the_terminal_body_alone(a_body):
    """The composer streams either way. A JSON caller's pieces are drained, not
    framed — the body is the same one they have always received."""

    app, _ = client()

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    assert response.status_code == 200
    body = response.json()
    assert body["message"]["outcome"]["status"] == "answered"
    assert "sequenceNumber" not in body["context"]
    assert [b["type"] for b in body["message"]["content"]] == ["text"]


def test_both_modes_describe_the_same_turn(a_body):
    streaming, _ = client()
    plain, _ = client()

    stream = streaming.post("/v1/turns", json=a_body(), headers={"Accept": SSE})
    single = plain.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    terminal = frames(stream.text)[-1][1]
    assert terminal["message"] == single.json()["message"]


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


def test_an_unknown_field_is_ignored(a_body):
    """A caller can add a field before the DSS knows it."""
    app, runner = client()
    body = a_body()
    body["message"]["attributes"]["temperature"] = 0.7

    response = app.post("/v1/turns", json=body)

    assert response.status_code == 200
    assert len(runner.calls) == 1


def test_no_sender_id_means_the_host_network(a_body):
    app, runner = client(network_sender_id="host.example.org")

    app.post("/v1/turns", json=a_body())

    [(_, ctx)] = runner.calls
    assert ctx.sender_id == "host.example.org"


def test_a_missing_required_field_is_unprocessable(a_body):
    app, runner = client()
    body = a_body()
    del body["context"]["sessionId"]

    response = app.post("/v1/turns", json=body)

    assert response.status_code == 422
    assert runner.calls == []


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


@pytest.mark.parametrize("accept", [JSON, "*/*", None])
def test_anything_but_the_event_stream_gets_json(a_body, accept):
    """JSON is both the default and the fallback: an absent `Accept`, a
    wildcard, and an explicit request all resolve to it."""

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


# def test_a_saturated_dss_says_come_back_later(a_body):
#     app, _ = client(max_concurrent_turns=0)

#     response = app.post("/v1/turns", json=a_body())

#     assert response.status_code == 429
#     assert response.headers["retry-after"]


def test_an_unready_dss_is_unavailable(a_body):
    app, _ = client(ready=False)

    assert app.post("/v1/turns", json=a_body()).status_code == 503


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


def test_a_fault_mid_stream_keeps_the_frames_already_sent(a_body):
    """HTTP was 200 before anyone knew, so it stays 200. The terminal event is
    where the failure is reported."""

    app, _ = client(FakeRunner(EVENTS, fail_after=2))

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": SSE})

    assert response.status_code == 200
    sent = frames(response.text)
    assert [name for name, _ in sent] == [
        "turn.created",
        "claim.delta",
        "turn.failed",
    ]
    assert sent[-1][1]["message"]["outcome"]["cause"] == "internal"


def test_a_fault_in_json_mode_is_still_a_two_hundred(a_body):
    """Not a 502: a dependency failure always lands in the outcome."""

    app, _ = client(FakeRunner(EVENTS, fail_after=0))

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    assert response.status_code == 200
    assert response.json()["message"]["outcome"]["status"] == "unavailable"


def test_a_form_arrives_whole_in_its_own_completed_claim(a_body):
    app, _ = client(FakeRunner(FORM_EVENTS))

    sent = frames(app.post("/v1/turns", json=a_body(), headers={"Accept": SSE}).text)

    assert [name for name, _ in sent] == [
        "turn.created",
        "claim.delta",
        "claim.delta",
        "claim.completed",
        "claim.completed",
        "turn.completed",
    ]
    claimed = [
        f["message"]["content"][0]["type"] for n, f in sent if n == "claim.completed"
    ]
    assert claimed == ["text", "form"]
    assert all(
        f["message"]["content"][0]["type"] != "form"
        for n, f in sent
        if n == "claim.delta"
    )


def test_a_json_turn_carries_the_text_then_the_form(a_body):
    streaming, _ = client(FakeRunner(FORM_EVENTS))
    plain, _ = client(FakeRunner(FORM_EVENTS))

    stream = streaming.post("/v1/turns", json=a_body(), headers={"Accept": SSE})
    single = plain.post("/v1/turns", json=a_body(), headers={"Accept": JSON})

    body = single.json()["message"]
    assert body["outcome"] == {
        "status": "requires_input",
        "confidence": 80,
        "cause": None,
    }
    assert [b["type"] for b in body["content"]] == ["text", "form"]
    assert body["content"][1]["formId"] == "8f3c"
    assert frames(stream.text)[-1][1]["message"] == body


def test_text_and_a_form_in_one_message_is_unprocessable(a_body):
    """One kind of content per message, so a turn runs on one path."""
    app, runner = client()
    form = {
        "type": "form",
        "formId": "offer-1",
        "context": {"scheme": "PMFBY", "category": "Grievance", "action": "support"},
        "submissionId": "submission-1",
        "fields": [],
    }
    thread = [{"role": "user", "content": [{"type": "text", "text": "hi"}, form]}]

    response = app.post("/v1/turns", json=a_body(message__input=thread))

    assert response.status_code == 422
    assert runner.calls == []
