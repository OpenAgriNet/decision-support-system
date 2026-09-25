"""Tier 2 — finding one turn's trace in Langfuse v4 and reading it.

Against a local server replaying a real v4 observations response. Two steps,
because only `dss.turn` carries the session: find the root by session, then
fetch every observation of its trace.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from evals.perf.traces import fetch_trace

_TRACE = (
    Path(__file__).parents[3] / "unit" / "evals" / "perf" / "fixtures"
) / "langfuse_trace.json"
_SINCE = datetime(2026, 9, 23, 13, 0, tzinfo=UTC)


def _observations(request: Request) -> Response:
    rows = json.loads(_TRACE.read_text())["data"]
    if "sessionId" in request.args:
        rows = [r for r in rows if r["name"] == "dss.turn"]
    elif request.args.get("traceId") != rows[0]["traceId"]:
        rows = []
    return Response(json.dumps({"data": rows, "meta": {}}), mimetype="application/json")


async def test_a_turns_trace_is_found_by_its_session_and_read_whole(
    httpserver: HTTPServer,
):
    httpserver.expect_request("/api/public/v2/observations").respond_with_handler(
        _observations
    )

    async with httpx.AsyncClient(base_url=httpserver.url_for("/")) as client:
        facts = await fetch_trace(
            client, "bench-spike-2d8658b3", since=_SINCE, deadline_s=1, poll_s=0.05
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
            client, "bench-never", since=_SINCE, deadline_s=0.2, poll_s=0.05
        )

    assert facts is None
