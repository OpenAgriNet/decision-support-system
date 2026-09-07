"""The HTTP rules for `POST /v1/turns`.

Admission is ordered cheapest-rejection-first, so a flood of bad requests costs
as little as possible: capacity, then readiness, then media type, then the body,
then the schema, then negotiation.

The one invariant that shapes everything else: **once the first response byte is
written, the status code cannot change.** So every failure discovered after that
point becomes a terminal event inside a 200, and a dependency failure never
surfaces as a 502.

This module constructs nothing. It is handed a `TurnRunner` and never learns
which implementation it got.

The handler takes a raw `Request` rather than a bound model, because the
400-vs-422 split and the decompressed-size cap both need the bytes before
anything validates them. That means FastAPI cannot infer the request schema, so
it is declared explicitly in `openapi_extra` — the schema comes from the wire
models, which *are* the contract.
"""

from __future__ import annotations

import gzip
import json
import uuid
import zlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

import anyio
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from dss.adapters.http.v1 import mapping, problem, schema, sse
from dss.config.settings import Settings
from dss.core.shared.models import (
    Cause,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
)
from dss.ports.turn import TurnRunner

JSON_MEDIA_TYPE = "application/json"
SSE_MEDIA_TYPE = "text/event-stream"
RETRY_AFTER_SECONDS = "1"


def turn_router(
    *,
    runner: TurnRunner,
    settings: Settings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    limiter = anyio.Semaphore(max(settings.max_concurrent_turns, 1))
    saturated = settings.max_concurrent_turns == 0

    async def endpoint(request: Request) -> Response:
        if saturated:
            return problem.problem(
                429,
                problem.CAPACITY,
                "The DSS is at its concurrency cap.",
                headers={"Retry-After": RETRY_AFTER_SECONDS},
            )
        if not settings.ready:
            return problem.problem(
                503, problem.NOT_READY, "The DSS is not ready to serve turns."
            )

        media_type = request.headers.get("content-type", "").split(";")[0].strip()
        if media_type != JSON_MEDIA_TYPE:
            return problem.problem(
                415,
                problem.UNSUPPORTED_MEDIA_TYPE,
                f"Send {JSON_MEDIA_TYPE}.",
            )

        wants_stream = _wants_stream(request.headers.get("accept"))
        if wants_stream is None:
            return problem.problem(
                406,
                problem.NOT_ACCEPTABLE,
                f"This endpoint serves {JSON_MEDIA_TYPE} or {SSE_MEDIA_TYPE}.",
            )

        try:
            raw = _decoded_body(
                await request.body(),
                encoding=request.headers.get("content-encoding"),
                cap=settings.max_body_bytes,
            )
        except _TooLarge:
            return problem.problem(
                413,
                problem.PAYLOAD_TOO_LARGE,
                f"The decompressed body exceeds {settings.max_body_bytes} bytes.",
            )
        except _Undecodable as exc:
            return problem.problem(
                400,
                problem.MALFORMED,
                f"Body is not valid gzip: {exc}",
            )

        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return problem.problem(400, problem.MALFORMED, f"Body is not JSON: {exc}")

        try:
            body = schema.TurnRequest.model_validate(payload)
        except ValidationError as exc:
            return problem.problem(422, problem.INVALID, exc.json())

        async with limiter:
            ctx = mapping.to_turn_context(body, message_id=_minted_id())
            try:
                turn = mapping.to_user_turn(body)
            except ValueError as exc:
                # Well-formed against the schema, but not a turn the DSS can act
                # on — a thread whose last message is not the farmer's, for
                # instance. The schema cannot express that, so it lands here.
                return problem.problem(422, problem.INVALID, str(exc))
            response_id = _minted_id()

            if wants_stream:
                stream = sse.Stream(ctx, response_id=response_id, clock=clock)
                return StreamingResponse(
                    _frames(runner, turn, ctx, stream),
                    media_type=SSE_MEDIA_TYPE,
                )
            return await _single(runner, turn, ctx, response_id, clock)

    router = APIRouter()
    router.add_api_route(
        "/v1/turns",
        endpoint,
        methods=["POST"],
        response_model=None,
        summary="Run one turn",
        openapi_extra=_OPENAPI,
    )
    return router


def _ref(model: type) -> dict[str, str]:
    """Point at the published component rather than inlining the schema.

    Inlining carries Pydantic's `#/$defs/...` references into the document,
    where they resolve against the root and dangle. `entrypoint/app.py`
    publishes the definitions; this only names them.
    """

    return {"$ref": schema.REF_TEMPLATE.format(model=model.__name__)}


_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {JSON_MEDIA_TYPE: {"schema": _ref(schema.TurnRequest)}},
    },
    "responses": {
        "200": {
            "description": (
                "The turn was processed. Refusals, no-matches and dependency "
                "failures all arrive here — read message.outcome.status."
            ),
            "content": {
                JSON_MEDIA_TYPE: {"schema": _ref(schema.TurnResponse)},
                SSE_MEDIA_TYPE: {
                    "schema": {
                        "type": "string",
                        "description": (
                            "turn.created, then one claim.completed per reviewed "
                            "claim, then turn.completed or turn.failed. Each frame's "
                            "data is a TurnResponse."
                        ),
                    }
                },
            },
        },
        "400": {"description": "Body is not JSON"},
        "406": {"description": "Accept is neither JSON nor the event stream"},
        "413": {"description": "Decompressed body over the cap"},
        "415": {"description": "Content-Type is not application/json"},
        "422": {"description": "Well-formed JSON that violates the contract"},
        "429": {"description": "Concurrency cap reached; carries Retry-After"},
        "503": {"description": "The DSS is not ready"},
    },
}


async def _frames(
    runner: TurnRunner, turn, ctx: TurnContext, stream: sse.Stream
) -> AsyncIterator[bytes]:
    """Forward each event as it arrives.

    A fault here cannot change the status code — it was written when the stream
    opened — so it is reported as a terminal event instead.
    """

    try:
        async for event in runner.run(turn, ctx):
            yield stream.frame(event)
    except Exception:
        yield stream.frame(_internal_failure())


async def _single(
    runner: TurnRunner,
    turn,
    ctx: TurnContext,
    response_id: str,
    clock: Callable[[], datetime],
) -> JSONResponse:
    """Drain the turn and render only its terminal event.

    Claims are discarded on purpose: `content` on the terminal event already
    repeats them, and a caller in this mode never saw the stream.
    """

    finished: TurnFinished | None = None
    try:
        async for event in runner.run(turn, ctx):
            if isinstance(event, TurnFinished):
                finished = event
    except Exception:
        finished = _internal_failure()

    if finished is None:
        finished = _internal_failure()

    frame = mapping.to_terminal_frame(
        finished, ctx, now=clock(), seq=None, response_id=response_id
    )
    return JSONResponse(
        json.loads(frame.model_dump_json(by_alias=True, exclude_none=True)),
        media_type=JSON_MEDIA_TYPE,
    )


def _internal_failure() -> TurnFinished:
    """What a caller gets when the turn broke and said nothing itself."""

    return TurnFinished(
        outcome=TurnOutcome(
            status=TurnStatus.UNAVAILABLE,
            cause=Cause.INTERNAL,
            confidence=0,
        )
    )


def _wants_stream(accept: str | None) -> bool | None:
    """`True` for SSE, `False` for JSON, `None` when neither can be served."""

    if not accept:
        return False
    offered = {part.split(";")[0].strip() for part in accept.split(",")}
    if SSE_MEDIA_TYPE in offered:
        return True
    if offered & {JSON_MEDIA_TYPE, "*/*", "application/*"}:
        return False
    return None


class _TooLarge(Exception):
    pass


class _Undecodable(Exception):
    """The body could not be decompressed. A caller's broken client, not ours."""


def _decoded_body(raw: bytes, *, encoding: str | None, cap: int) -> bytes:
    """Decompress if asked, and enforce the cap on the *decompressed* size.

    A compressed length proves nothing — a few hundred bytes of gzip expands to
    megabytes, which is the whole shape of a decompression bomb.
    """

    if (encoding or "").strip().lower() == "gzip":
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError, zlib.error) as exc:
            # A body that is not gzip, or is truncated. Both are the caller's
            # mistake, and neither may take the process down.
            raise _Undecodable(str(exc)) from exc
    if len(raw) > cap:
        raise _TooLarge
    return raw


def _minted_id() -> str:
    return uuid.uuid4().hex


__all__ = ["TurnStarted", "turn_router"]
