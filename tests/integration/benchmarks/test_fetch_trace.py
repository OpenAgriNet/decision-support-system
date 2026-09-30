"""Tier 2 — finding one turn's trace in Langfuse v4 and reading it.

Against a local server replaying a real v4 observations response. Found by
the trace id the benchmark sent as `traceparent`: a run's turns share one
session, so the session no longer picks out a turn.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from benchmarks.traces import fetch_trace

_TRACE = (
    Path(__file__).parents[2] / "unit" / "benchmarks" / "fixtures"
) / "langfuse_trace.json"
_SINCE = datetime(2026, 9, 23, 13, 0, tzinfo=UTC)


_ROWS = json.loads(_TRACE.read_text())["data"]
_TRACE_ID = _ROWS[0]["traceId"]


def _serving(rows: list[dict]):
    def handler(request: Request) -> Response:
        found = rows if request.args.get("traceId") == _TRACE_ID else []
        return Response(
            json.dumps({"data": found, "meta": {}}), mimetype="application/json"
        )

    return handler


async def test_a_turns_trace_is_found_by_its_trace_id_and_read_whole(
    httpserver: HTTPServer,
):
    httpserver.expect_request("/api/public/v2/observations").respond_with_handler(
        _serving(_ROWS)
    )

    async with httpx.AsyncClient(base_url=httpserver.url_for("/")) as client:
        facts = await fetch_trace(
            client, _TRACE_ID, since=_SINCE, deadline_s=1, poll_s=0.05
        )

    assert facts is not None
    assert facts.stages["planner"] > 2
    assert len(facts.calls) == 6


async def test_a_trace_never_ingested_gives_none_by_the_deadline(
    httpserver: HTTPServer,
):
    """Langfuse may lag or drop a trace. The turn's client timings still
    count; only its stage and token figures are missing."""

    httpserver.expect_request("/api/public/v2/observations").respond_with_json(
        {"data": [], "meta": {}}
    )

    async with httpx.AsyncClient(base_url=httpserver.url_for("/")) as client:
        facts = await fetch_trace(
            client, "0" * 32, since=_SINCE, deadline_s=0.2, poll_s=0.05
        )

    assert facts is None


async def test_a_trace_still_missing_its_turn_span_is_not_read_yet(
    httpserver: HTTPServer,
):
    """`dss.turn` ends last, so until it arrives the trace is incomplete. Read
    early, it would report a turn with some of its stages missing."""

    partial = [r for r in _ROWS if r["name"] != "dss.turn"]
    httpserver.expect_request("/api/public/v2/observations").respond_with_handler(
        _serving(partial)
    )

    async with httpx.AsyncClient(base_url=httpserver.url_for("/")) as client:
        facts = await fetch_trace(
            client, _TRACE_ID, since=_SINCE, deadline_s=0.2, poll_s=0.05
        )

    assert facts is None
