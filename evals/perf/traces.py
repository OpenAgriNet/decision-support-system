"""Reading one turn's trace back from Langfuse: stage times and tokens.

Over Langfuse v4's public REST API (`/api/public/v2/observations`), not its
SDK: nothing in this repo imports `langfuse`, so the tracing backend stays a
config change (ADR-0007).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import anyio
import httpx

# The field groups read here: ids and times, names, tokens, span attributes.
_FIELDS = "core,basic,usage,metadata"
# Span name → the stage it times.
_TIMED = {"dss.discover": "discover", "dss.select": "select"}
_STAGE_PREFIX = "dss.stage."
# The agents whose model `dss.turn` records, as `attributes.<agent>_model`.
_AGENTS = ("intent", "moderation", "planner", "composer")


@dataclass(frozen=True)
class TokenCall:
    """One model call's tokens, and the agent that made it."""

    agent: str
    input: int
    output: int


@dataclass(frozen=True)
class TraceFacts:
    # Seconds per stage. Spans of one stage that overlap count once.
    stages: dict[str, float]
    calls: list[TokenCall]
    # Agent → the model it ran on, as the server recorded it on `dss.turn`.
    models: dict[str, str]
    # A stage that is not a child of `dss.turn` lost its context across a
    # task boundary; its time then says nothing about this turn.
    flat: bool


async def fetch_trace(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    since: datetime,
    deadline_s: float = 60,
    poll_s: float = 5,
) -> TraceFacts | None:
    """One turn's trace, or None if Langfuse has not ingested it by the
    deadline.

    Two steps, because only `dss.turn` carries the session: find that root,
    then fetch every observation of its trace. The root ends last, so once it
    is visible its children have been exported too.
    """

    params = {"fromStartTime": since.isoformat(), "limit": 100}
    waited = 0.0
    while True:
        roots = await _observations(client, {**params, "sessionId": session_id})
        if roots:
            break
        if waited >= deadline_s:
            return None
        await anyio.sleep(poll_s)
        waited += poll_s
    return to_trace_facts(
        await _observations(client, {**params, "traceId": roots[0]["traceId"]})
    )


async def _observations(client: httpx.AsyncClient, params: dict) -> list[dict]:
    response = await client.get(
        "/api/public/v2/observations", params={**params, "fields": _FIELDS}
    )
    response.raise_for_status()
    return response.json()["data"]


def to_trace_facts(observations: list[dict]) -> TraceFacts:
    by_id = {row["id"]: row for row in observations}
    spans: dict[str, list[tuple[datetime, datetime]]] = {}
    calls: list[TokenCall] = []
    for row in observations:
        stage = _stage_of(row["name"])
        if stage is not None:
            spans.setdefault(stage, []).append(_interval(row))
        # Only the model calls: an agent's own span sums its calls' tokens,
        # so counting both would count every token twice.
        if row["type"] == "GENERATION":
            calls.append(
                TokenCall(
                    agent=_agent_of(row, by_id),
                    input=row["inputUsage"],
                    output=row["outputUsage"],
                )
            )
    return TraceFacts(
        stages={stage: _covered(intervals) for stage, intervals in spans.items()},
        calls=calls,
        models=_models(observations),
        flat=_is_flat(observations),
    )


def _is_flat(observations: list[dict]) -> bool:
    turn = next((row for row in observations if row["name"] == "dss.turn"), None)
    turn_id = turn["id"] if turn else None
    return any(
        row["parentObservationId"] != turn_id
        for row in observations
        if row["name"].startswith(_STAGE_PREFIX)
    )


def _models(observations: list[dict]) -> dict[str, str]:
    turn = next((row for row in observations if row["name"] == "dss.turn"), None)
    metadata = (turn or {}).get("metadata") or {}
    return {
        agent: metadata[f"attributes.{agent}_model"]
        for agent in _AGENTS
        if f"attributes.{agent}_model" in metadata
    }


def _agent_of(row: dict, by_id: dict[str, dict]) -> str:
    """The agent a model call belongs to: its nearest `AGENT` ancestor, whose
    name is `<agent> run`."""

    parent = by_id.get(row["parentObservationId"])
    while parent is not None and parent["type"] != "AGENT":
        parent = by_id.get(parent["parentObservationId"])
    return parent["name"].removesuffix(" run") if parent else "unknown"


def _stage_of(name: str) -> str | None:
    if name.startswith(_STAGE_PREFIX):
        return name.removeprefix(_STAGE_PREFIX)
    return _TIMED.get(name)


def _interval(row: dict) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(row["startTime"]), datetime.fromisoformat(
        row["endTime"]
    )


def _covered(intervals: list[tuple[datetime, datetime]]) -> float:
    """Seconds covered by the spans, overlaps counted once.

    Parallel calls (two /discover at once) must not be summed, and calls apart
    in time (/select, then the planner thinks, then /select again) must not
    count the gap between them. Merging overlaps, then adding up, does both.
    """

    total = 0.0
    end = None
    for started, ended in sorted(intervals):
        if end is None or started > end:
            total += (ended - started).total_seconds()
            end = ended
        elif ended > end:
            total += (ended - end).total_seconds()
            end = ended
    return total
