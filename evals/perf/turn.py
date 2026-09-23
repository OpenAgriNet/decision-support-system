"""Sending one benchmark question to a running DSS, and timing it.

Timed by the client with `perf_counter`, not read from spans: a span sees
only the server's side, and the farmer waits for the network too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

import httpx

from evals.perf.questions import Question
from evals.perf.sse import parse_sse


@dataclass(frozen=True)
class TurnTiming:
    status: str | None
    # Seconds from sending the request. None when the turn streamed no answer.
    first_delta_s: float | None
    total_s: float | None


async def run_turn(
    client: httpx.AsyncClient,
    base_url: str,
    question: Question,
    *,
    session_id: str,
    transaction_id: str,
) -> TurnTiming:
    body = turn_request(question, session_id, transaction_id)
    status = first_delta_s = total_s = None
    started = perf_counter()
    async with client.stream(
        "POST",
        f"{base_url}/v1/turns",
        json=body,
        headers={"Accept": "text/event-stream"},
        timeout=120,
    ) as response:
        # A refused request is not a fast turn: timed, it would read as
        # thousands of turns a minute.
        if response.status_code != 200:
            return TurnTiming(
                status=f"http_{response.status_code}", first_delta_s=None, total_s=None
            )
        async for event, data in parse_sse(response.aiter_lines()):
            if event == "claim.delta" and first_delta_s is None:
                first_delta_s = perf_counter() - started
            elif event in ("turn.completed", "turn.failed"):
                total_s = perf_counter() - started
                status = data["message"]["outcome"]["status"]
    return TurnTiming(status=status, first_delta_s=first_delta_s, total_s=total_s)


def turn_request(question: Question, session_id: str, transaction_id: str) -> dict:
    """The `POST /v1/turns` body for one question, as a client app sends it."""

    location: dict = {"region": question.region, "area": question.area}
    if question.point is not None:
        location["geometry"] = {"type": "Point", "coordinates": list(question.point)}
    return {
        "context": {
            "id": "api.dss.turn",
            "timestamp": datetime.now(UTC).isoformat(),
            "sessionId": session_id,
            "transactionId": transaction_id,
        },
        "message": {
            "input": [
                {"role": "user", "content": [{"type": "text", "text": question.text}]}
            ],
            "attributes": {
                "channel": "web",
                "sourceLanguage": "en",
                "targetLanguage": "en",
                "location": location,
            },
        },
    }
