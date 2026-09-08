"""What the transport does when the runner breaks.

Two rules, and the second is the one a bare `except Exception` gets wrong:

- A crash must reach the caller as a terminal event, never a 5xx, because the
  status code was already written.
- A *cancellation* is not a crash and must propagate. anyio's cancelled class is
  `asyncio.CancelledError`, which derives from **BaseException**, so
  `except Exception` cannot catch it — a fact worth a test rather than a
  comment, because it is the whole reason the broad catch is safe here.
"""

from __future__ import annotations

import logging

import anyio
from fastapi.testclient import TestClient

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
EVENTS = [TurnStarted(), Claim(content=ANSWER.content[0]), ANSWER]


class ExplodingRunner:
    """Raises a plain error part-way through the stream."""

    def __init__(self, *, after: int = 1) -> None:
        self._after = after

    async def run(self, turn, ctx):
        for index, event in enumerate(EVENTS):
            if index == self._after:
                raise RuntimeError("the runner fell over")
            yield event


class CancellingRunner:
    """Raises what anyio raises when a client disconnects."""

    def __init__(self, *, after: int = 1) -> None:
        self._after = after

    async def run(self, turn, ctx):
        for index, event in enumerate(EVENTS):
            if index == self._after:
                raise anyio.get_cancelled_exc_class()()
            yield event


def _client(runner) -> TestClient:
    return TestClient(
        build_app(runner=runner, settings=Settings()),
        raise_server_exceptions=False,
    )


def test_a_crash_mid_stream_becomes_a_terminal_event(a_body):
    response = _client(ExplodingRunner()).post(
        "/v1/turns", json=a_body(), headers={"Accept": "text/event-stream"}
    )

    assert response.status_code == 200
    assert "turn.failed" in response.text


def test_a_crash_is_logged_with_the_trace_id(a_body, caplog):
    """A crash that only reaches the caller as "could not be reached" is
    invisible to whoever has to fix it. The trace id is what joins the log line
    to the evidence files."""

    with caplog.at_level(logging.ERROR):
        _client(ExplodingRunner()).post(
            "/v1/turns", json=a_body(), headers={"Accept": "text/event-stream"}
        )

    assert any(r.levelno >= logging.ERROR for r in caplog.records), "nothing logged"
    assert "the runner fell over" in caplog.text, "the traceback is missing"
    # In the message, not only in `extra`: the default formatter drops extras, so
    # a trace id passed that way is invisible to whoever reads the logs.
    assert "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44" in caplog.text


def test_a_crash_in_json_mode_is_logged_too(a_body, caplog):
    with caplog.at_level(logging.ERROR):
        response = _client(ExplodingRunner(after=0)).post(
            "/v1/turns", json=a_body(), headers={"Accept": JSON}
        )

    assert response.json()["message"]["outcome"]["status"] == "unavailable"
    assert "the runner fell over" in caplog.text


def test_the_cancelled_class_is_not_an_exception_subclass():
    """Why the broad catch above is safe, asserted rather than asserted-in-prose.

    If a future backend made cancellation `Exception`-derived, the catch would
    start swallowing client disconnects and this fails first.
    """

    async def check() -> bool:
        return issubclass(anyio.get_cancelled_exc_class(), Exception)

    assert anyio.run(check) is False


def test_a_cancellation_does_not_become_a_failed_turn(a_body):
    """A disconnect must not be reported as a turn outcome. The stream is simply
    abandoned — no terminal frame, because there is nobody left to read one."""

    response = _client(CancellingRunner()).post(
        "/v1/turns", json=a_body(), headers={"Accept": "text/event-stream"}
    )

    assert "turn.failed" not in response.text
    assert "turn.completed" not in response.text


def test_a_cancellation_in_json_mode_produces_no_turn_body(a_body):
    response = _client(CancellingRunner()).post(
        "/v1/turns", json=a_body(), headers={"Accept": JSON}
    )

    assert response.status_code != 200, "a cancelled turn has no answer to give"
