"""Bodies for the responses that never reach the runner.

The contract fixes the status codes but not the shape of a 4xx body — it only
specifies the turn envelope, which these are not. One flat shape is used until
the contract says otherwise; see the open item in the plan.

Nothing here is farmer-facing: a caller that sent a malformed envelope has a bug
to fix, and no farmer is waiting on this text.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

MALFORMED = "malformed_request"
INVALID = "invalid_request"
UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
PAYLOAD_TOO_LARGE = "payload_too_large"
NOT_ACCEPTABLE = "not_acceptable"
CAPACITY = "capacity_reached"
NOT_READY = "not_ready"


def problem(
    status: int, code: str, message: str, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    return JSONResponse(body, status_code=status, headers=headers)
