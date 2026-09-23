"""Tier 1 — reading the DSS's SSE stream as frames.

The benchmark times each frame as it arrives, so the parser yields a frame as
soon as its blank line is read, not after the whole stream.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from evals.perf.sse import parse_sse


async def _lines(*lines: str) -> AsyncIterator[str]:
    for line in lines:
        yield line


async def test_each_frame_becomes_one_event_with_its_data():
    lines = _lines(
        "event: turn.created",
        'data: {"n": 1}',
        "",
        "event: claim.delta",
        'data: {"n": 2}',
        "",
    )

    frames = [frame async for frame in parse_sse(lines)]

    assert frames == [("turn.created", {"n": 1}), ("claim.delta", {"n": 2})]
