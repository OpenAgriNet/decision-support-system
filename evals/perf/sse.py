"""Reading the DSS's SSE stream as `(event, data)` frames.

The DSS writes each frame as `event: <name>`, then `data: <json>`, then a
blank line (`adapters/http/v1/sse.py`). A frame is yielded as soon as its blank
line arrives, so the caller can time it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator


async def parse_sse(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, dict]]:
    event = data = None
    async for line in lines:
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = line.removeprefix("data: ")
        elif line == "" and event is not None and data is not None:
            yield event, json.loads(data)
            event = data = None
