"""SSE framing. Owns the event names and the sequence counter, nothing else."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from dss.adapters.http.v1 import sse
from dss.core.shared.models import (
    Cause,
    Claim,
    TextBlock,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)

NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


@pytest.fixture
def stream():
    ctx = TurnContext(trace_id="trc_1", session_id="conv_1", message_id="msg_in")
    return sse.Stream(ctx, response_id="msg_out_1", clock=lambda: NOW)


def _finished(status: TurnStatus, cause: Cause | None = None) -> TurnFinished:
    return TurnFinished(outcome=TurnOutcome(status=status, cause=cause, confidence=50))


def _claim(text: str) -> Claim:
    return Claim(content=TextBlock(text=text))


def test_a_frame_is_an_event_line_a_data_line_and_a_blank_line(stream):
    frame = stream.frame(TurnStarted())

    # SSE terminates a frame with a blank line, so the trailing "\n\n" splits
    # into two empty strings.
    head, body, blank, end = frame.decode().split("\n")
    assert head == "event: turn.created"
    assert body.startswith("data: ")
    assert (blank, end) == ("", "")
    assert json.loads(body.removeprefix("data: "))["context"]["traceId"] == "trc_1"


def test_the_sequence_starts_at_one_and_never_skips(stream):
    events = [
        TurnStarted(),
        _claim("one"),
        _claim("two"),
        _finished(TurnStatus.ANSWERED),
    ]

    numbers = [_seq(stream.frame(e)) for e in events]

    # The contract sets `sequenceNumber` minimum 1.
    assert numbers == [1, 2, 3, 4]


def test_an_unavailable_turn_is_named_a_failure(stream):
    frame = stream.frame(_finished(TurnStatus.UNAVAILABLE, Cause.PROVIDER_UNAVAILABLE))

    assert _event(frame) == "turn.failed"


@pytest.mark.parametrize(
    "status",
    [
        TurnStatus.ANSWERED,
        TurnStatus.PARTIALLY_ANSWERED,
        TurnStatus.REJECTED,
        TurnStatus.NO_MATCH,
        TurnStatus.REQUIRES_INPUT,
    ],
)
def test_every_other_terminal_status_is_a_completion(stream, status):
    """A refusal is a turn the DSS completed. Only a dependency failure is a
    failed turn, so a caller can tell the two apart without reading prose."""

    assert _event(stream.frame(_finished(status))) == "turn.completed"


def test_a_claim_is_named_for_the_claim_being_complete(stream):
    stream.frame(TurnStarted())

    assert _event(stream.frame(_claim("one"))) == "claim.completed"


def test_no_frame_carries_an_id_line(stream):
    """An `id:` line advertises `Last-Event-ID`, and this stream cannot be
    resumed — the DSS finishes the turn server-side instead."""

    frames = [stream.frame(TurnStarted()), stream.frame(_claim("one"))]

    lines = [line for f in frames for line in f.decode().split("\n")]
    assert not any(line.startswith("id:") for line in lines)


def _event(frame: bytes) -> str:
    return frame.decode().split("\n")[0].removeprefix("event: ")


def _seq(frame: bytes) -> int:
    body = frame.decode().split("\n")[1].removeprefix("data: ")
    return json.loads(body)["context"]["sequenceNumber"]
