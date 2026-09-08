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

import json
import logging
import uuid
import zlib
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass
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

logger = logging.getLogger(__name__)

JSON_MEDIA_TYPE = "application/json"
SSE_MEDIA_TYPE = "text/event-stream"
RETRY_AFTER_SECONDS = "1"


@dataclass(frozen=True)
class _Admitted:
    """A request that passed every gate, and the mode it asked for."""

    body: schema.TurnRequest
    wants_stream: bool


def turn_router(
    *,
    runner: TurnRunner,
    settings: Settings,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    limiter = anyio.Semaphore(max(settings.max_concurrent_turns, 1))
    saturated = settings.max_concurrent_turns == 0

    async def endpoint(request: Request) -> Response:
        admitted = await _admit(request, settings, saturated=saturated)
        if isinstance(admitted, Response):
            return admitted
        async with limiter:
            return await _run(admitted, runner=runner, clock=clock)

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


async def _admit(
    request: Request, settings: Settings, *, saturated: bool
) -> Response | _Admitted:
    """Every gate a request must pass, in order — or the rejection it earned.

    **The order is the design**, which is why it stays in one function rather
    than one function per step: cheapest rejection first, so a flood of bad
    requests costs as little as possible. Capacity and readiness are answered
    from memory; the media type from a header; only then is the body read, and
    only then parsed.
    """

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
            415, problem.UNSUPPORTED_MEDIA_TYPE, f"Send {JSON_MEDIA_TYPE}."
        )

    wants_stream = _wants_stream(request.headers.get("accept"))
    if wants_stream is None:
        return problem.problem(
            406,
            problem.NOT_ACCEPTABLE,
            f"This endpoint serves {JSON_MEDIA_TYPE} or {SSE_MEDIA_TYPE}.",
        )

    try:
        raw = await _decoded_body(
            request.stream(),
            encoding=request.headers.get("content-encoding"),
            cap=settings.max_body_bytes,
            declared=_declared_length(request),
        )
    except _TooLarge:
        return problem.problem(
            413,
            problem.PAYLOAD_TOO_LARGE,
            f"The decompressed body exceeds {settings.max_body_bytes} bytes.",
        )
    except _Undecodable as exc:
        return problem.problem(400, problem.MALFORMED, f"Body is not valid gzip: {exc}")

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return problem.problem(400, problem.MALFORMED, f"Body is not JSON: {exc}")

    try:
        body = schema.TurnRequest.model_validate(payload)
    except ValidationError as exc:
        return problem.problem(422, problem.INVALID, exc.json())

    return _Admitted(body=body, wants_stream=wants_stream)


async def _run(
    admitted: _Admitted, *, runner: TurnRunner, clock: Callable[[], datetime]
) -> Response:
    """Map the admitted request into the domain and dispatch it.

    One rejection survives past admission: the schema cannot express "the last
    message must be the farmer's", so a thread of only assistant messages is
    well-formed and still unusable. It is caught here rather than in `_admit`
    because only the mapping can tell.
    """

    ctx = mapping.to_turn_context(admitted.body, message_id=_minted_id())
    try:
        turn = mapping.to_user_turn(admitted.body)
    except ValueError as exc:
        return problem.problem(422, problem.INVALID, str(exc))

    response_id = _minted_id()
    if admitted.wants_stream:
        stream = sse.Stream(ctx, response_id=response_id, clock=clock)
        return StreamingResponse(
            _frames(runner, turn, ctx, stream), media_type=SSE_MEDIA_TYPE
        )
    return await _single(runner, turn, ctx, response_id, clock)


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
        # `except Exception` deliberately does not catch a cancellation: anyio's
        # cancelled class is `asyncio.CancelledError`, which derives from
        # BaseException, so a client disconnect propagates and unwinds rather
        # than being turned into a frame written to a closed socket.
        #
        # Logging matters because otherwise the crash reaches the caller as a
        # generic "could not be reached" and reaches nobody else at all. The
        # trace id goes in the message, not only in `extra` — the default
        # formatter drops extras, so an operator would never see it.
        logger.exception("turn failed mid-stream (trace_id=%s)", ctx.trace_id)
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
        # See `_frames` — a cancellation is BaseException-derived and propagates.
        logger.exception("turn failed (trace_id=%s)", ctx.trace_id)
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


def _declared_length(request: Request) -> int | None:
    """`Content-Length`, when it is present and a number.

    A malformed header is treated as absent rather than rejected — the streaming
    caps below catch an oversized body anyway, and refusing a request over a bad
    header would turn a caller's broken client into an outage.
    """

    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class _TooLarge(Exception):
    pass


class _Undecodable(Exception):
    """The body could not be decompressed. A caller's broken client, not ours."""


# zlib needs to be told the payload carries a gzip header rather than a raw
# deflate stream.
_GZIP_WBITS = zlib.MAX_WBITS | 16


async def _decoded_body(
    stream: AsyncIterable[bytes],
    *,
    encoding: str | None,
    cap: int,
    declared: int | None,
) -> bytes:
    """Read the body, decompressing if asked, without letting it choose how much
    memory we use.

    Three bounds, because a compressed length proves nothing — a few hundred
    bytes of gzip expands to megabytes, which is the whole shape of a
    decompression bomb:

    1. `Content-Length` over the cap is refused before a single byte is read.
    2. Bytes *read* are capped, so an incompressible stream cannot be used to
       push gigabytes through while the inflated output stays small.
    3. Bytes *produced* are capped as they are produced. `decompress(data, n)`
       yields at most `n` bytes and parks the rest in `unconsumed_tail`, so a
       non-empty tail means the payload wants more room than the cap allows.

    The peak allocation is therefore one chunk plus the cap, whatever the caller
    sends.
    """

    if declared is not None and declared > cap:
        raise _TooLarge

    gzipped = (encoding or "").strip().lower() == "gzip"
    inflater = zlib.decompressobj(_GZIP_WBITS) if gzipped else None
    body = bytearray()
    read = 0

    async for chunk in stream:
        read += len(chunk)
        if read > cap:
            raise _TooLarge
        if inflater is None:
            body += chunk
        else:
            try:
                body += inflater.decompress(chunk, cap + 1 - len(body))
            except zlib.error as exc:
                raise _Undecodable(str(exc)) from exc
            if inflater.unconsumed_tail:
                raise _TooLarge
        if len(body) > cap:
            raise _TooLarge

    if inflater is not None:
        try:
            body += inflater.flush()
        except zlib.error as exc:
            raise _Undecodable(str(exc)) from exc
        if not inflater.eof:
            # The stream ended mid-member: a truncated body.
            raise _Undecodable("gzip stream is incomplete")
        if len(body) > cap:
            raise _TooLarge

    return bytes(body)


def _minted_id() -> str:
    return uuid.uuid4().hex


__all__ = ["TurnStarted", "turn_router"]
