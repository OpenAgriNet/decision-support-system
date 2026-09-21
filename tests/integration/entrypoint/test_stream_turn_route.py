"""The streaming endpoint, driven through a real ASGI client against a fake runner.

`POST /v1/stream/turns` is the same turn as `/v1/turns` and a different
delivery: the composer's pieces leave as `claim.delta` frames while the rest is
still being written. Nothing below the port is wired — every case here is a
statement about the transport alone.

The two routes exist rather than one route with a flag because the runner is
never told which mode it is in (`ports/turn.py`): the route picks the runner,
and the runner is already wired to stream or not.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dss.config.settings import Settings
from dss.core.shared.models import (
    Cause,
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

WHOLE = "Rs 2,275 per quintal."
ANSWER = TurnFinished(
    outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
    content=(TextBlock(text=WHOLE, source_ids=()),),
)
STREAMED = [
    TurnStarted(),
    ClaimDelta(text="Rs 2,2"),
    ClaimDelta(text="75 per quin"),
    ClaimDelta(text="tal."),
    Claim(content=ANSWER.content[0]),
    ANSWER,
]


def client(runner=None, **overrides) -> tuple[TestClient, FakeRunner]:
    """Both routes mounted, each with its own runner — as the real app wires them."""

    stream_runner = runner or FakeRunner(STREAMED)
    settings = Settings(**overrides)
    app = build_app(
        runner=FakeRunner([TurnStarted(), Claim(content=ANSWER.content[0]), ANSWER]),
        stream_runner=stream_runner,
        settings=settings,
    )
    return TestClient(app), stream_runner


def _streamed(app: TestClient, body: dict) -> str:
    return app.post("/v1/stream/turns", json=body, headers={"Accept": SSE}).text


def frames(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        name = lines[0].removeprefix("event: ")
        out.append((name, json.loads(lines[1].removeprefix("data: "))))
    return out


def test_the_pieces_arrive_as_their_own_frames_before_the_finished_claim(a_body):
    app, _ = client()

    response = app.post("/v1/stream/turns", json=a_body(), headers={"Accept": SSE})

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


def test_the_delta_frames_join_back_to_the_completed_claim(a_body):
    """The guarantee a consumer renders on: concatenating the pieces gives the
    text the finished claim carries, character for character."""

    app, _ = client()

    sent = frames(_streamed(app, a_body()))

    deltas = [f for name, f in sent if name == "claim.delta"]
    completed = next(f for name, f in sent if name == "claim.completed")
    assert "".join(d["message"]["content"][0]["text"] for d in deltas) == WHOLE
    assert completed["message"]["content"][0]["text"] == WHOLE


def test_a_delta_carries_no_citations(a_body):
    """Mid-write the block has no end, so there is nothing to cite over. The
    annotations arrive with the completed claim."""

    app, _ = client()

    sent = frames(_streamed(app, a_body()))

    delta = next(f for name, f in sent if name == "claim.delta")
    block = delta["message"]["content"][0]
    assert block["type"] == "output_text_delta"
    assert "annotations" not in block
    assert delta["message"].get("outcome") is None


def test_the_sequence_never_skips_across_the_new_frames(a_body):
    app, _ = client()

    sent = frames(_streamed(app, a_body()))

    assert [f["context"]["sequenceNumber"] for _, f in sent] == [1, 2, 3, 4, 5, 6]


def test_a_json_only_caller_is_told_to_use_the_other_route(a_body):
    """This route streams. Asking it for one whole body is a caller mistake
    worth naming, not something to silently buffer."""

    app, _ = client()

    response = app.post("/v1/stream/turns", json=a_body(), headers={"Accept": JSON})

    assert response.status_code == 406
    assert "/v1/turns" in response.text


def test_a_failure_part_way_through_is_a_terminal_frame_not_a_status_code(a_body):
    """The status was written when the stream opened. Everything after it is an
    event — so a farmer gets an explanation rather than a half sentence."""

    app, _ = client(runner=FakeRunner(STREAMED, fail_after=3))

    response = app.post("/v1/stream/turns", json=a_body(), headers={"Accept": SSE})

    assert response.status_code == 200
    names = [name for name, _ in frames(response.text)]
    assert names == ["turn.created", "claim.delta", "claim.delta", "turn.failed"]
    failed = frames(response.text)[-1][1]["message"]
    assert failed["outcome"]["status"] == TurnStatus.UNAVAILABLE.value
    assert failed["error"]["code"] == Cause.INTERNAL.value


def test_the_existing_route_is_untouched(a_body):
    """No deltas, same three frames, same runner it always had."""

    app, _ = client()

    response = app.post("/v1/turns", json=a_body(), headers={"Accept": SSE})

    assert [name for name, _ in frames(response.text)] == [
        "turn.created",
        "claim.completed",
        "turn.completed",
    ]
