"""One log line per HTTP request, written once the response has gone out.

- Route, status, duration and the turn's ids. Never the body or the query
  string: both can hold the farmer's words.
- Plain ASGI, not Starlette's `BaseHTTPMiddleware`: that runs the app in
  another task, which would hide the ids a turn binds from this line.
- The health probe is left out; it polls every few seconds and says nothing.
  It is still logged when it fails.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dss.observability.log_fields import watch_request_turn_ids

logger = logging.getLogger("dss.http")

# Docker's healthcheck polls this; there is no separate health route.
HEALTH_PROBE_PATH = "/openapi.json"

# What a request that raised before answering is reported as, as Starlette does.
_UNHANDLED_STATUS = 500
# nginx's code for a client that closed the connection first.
_CLIENT_CLOSED_STATUS = 499


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_turn_ids = watch_request_turn_ids()
        start = time.monotonic()
        status_code = _UNHANDLED_STATUS

        async def send_and_note_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        def fields(status: int) -> dict[str, Any]:
            return _request_fields(scope, status, start, asdict(request_turn_ids.ids))

        try:
            await self._app(scope, receive, send_and_note_status)
        except Exception:
            logger.exception("request failed", extra=fields(_UNHANDLED_STATUS))
            raise
        except BaseException:
            # Cancellation: the client left, often mid-stream. Not a failure.
            logger.info("request cancelled", extra=fields(_CLIENT_CLOSED_STATUS))
            raise
        if _is_quiet_probe(scope, status_code):
            return
        logger.info("request done", extra=fields(status_code))


def _is_quiet_probe(scope: Scope, status_code: int) -> bool:
    return scope["path"] == HEALTH_PROBE_PATH and status_code < _UNHANDLED_STATUS


def _request_fields(
    scope: Scope, status_code: int, start: float, turn_ids: dict[str, str]
) -> dict[str, Any]:
    """OpenTelemetry's HTTP names, which HyperDX already knows; the turn's ids
    as the request saw them, since a streamed turn bound them in another task."""

    return {
        "http.request.method": scope["method"],
        "url.path": scope["path"],
        "http.response.status_code": status_code,
        "duration_ms": round((time.monotonic() - start) * 1000, 1),
        **turn_ids,
    }
