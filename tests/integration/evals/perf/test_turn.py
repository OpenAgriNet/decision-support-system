"""Tier 2 — driving one turn and timing it from the client.

Against a small local server that streams frames with set pauses, over a
real socket. An in-process transport would hand over the whole body at once,
and the time to the first piece would look the same as the total.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator

import anyio
import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from evals.perf.questions import Question
from evals.perf.turn import run_turn

AKOLA = Question(
    id="37-1",
    category="weather",
    text="Will it rain tomorrow in Akola district?",
    region="IN-MH",
    area="Akola",
    point=(77.056016, 20.748005),
)


def _frame(event: str, message: dict) -> str:
    return f"event: {event}\ndata: {json.dumps({'message': message})}\n\n"


def _answered_stream():
    async def frames():
        yield _frame("turn.created", {})
        await anyio.sleep(0.2)
        yield _frame("claim.delta", {"content": [{"text": "Rain "}]})
        await anyio.sleep(0.3)
        yield _frame("turn.completed", {"outcome": {"status": "answered"}})

    return frames()


@pytest.fixture
def dss() -> Iterator[str]:
    """A stand-in DSS on a free port, streaming the frames it is given."""

    app = FastAPI()

    @app.post("/v1/turns")
    async def turns() -> StreamingResponse:
        return StreamingResponse(_answered_stream(), media_type="text/event-stream")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join()


async def test_first_piece_arrives_before_the_turn_completes(dss: str):
    async with httpx.AsyncClient() as client:
        timing = await run_turn(
            client, dss, AKOLA, session_id="bench_s1", transaction_id="bench_t1"
        )

    assert timing.status == "answered"
    assert 0.15 < timing.first_delta_s < timing.total_s
    assert timing.total_s >= 0.45
