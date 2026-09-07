"""Wire <-> domain. Pure functions — the one module that speaks both vocabularies."""

from __future__ import annotations

from datetime import datetime

from dss.adapters.http.v1 import schema
from dss.core.shared.models import (
    ANONYMOUS,
    Cause,
    Channel,
    Claim,
    HistoryEntry,
    Location,
    OutputContent,
    Point,
    RefusalBlock,
    Role,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
    UserTurn,
)

# The concrete DSS release that handled the turn. Distinct from the envelope
# version: the contract shape and the build that served it move independently.
DSS_RELEASE = "v1.0.0"


def to_user_turn(body: schema.TurnRequest) -> UserTurn:
    """Normalize a validated request body into the turn the core works with."""

    thread = body.message.input
    current = _current_index(thread)

    attributes = body.message.attributes
    response = attributes.response

    return UserTurn(
        query=_text(thread[current]),
        source_lang=attributes.source_language,
        target_lang=attributes.target_language,
        channel=Channel(attributes.channel),
        response_max_chars=response.max_characters if response else None,
        user_id=_user_id(body.message.user_context),
        history=tuple(
            HistoryEntry(role=Role(m.role), content=_text(m)) for m in thread[:current]
        ),
        location=_location(attributes.location),
    )


def to_turn_context(body: schema.TurnRequest, *, message_id: str) -> TurnContext:
    """Assemble the turn's ids.

    The trace id **is** the caller's `transactionId` — the contract says
    `traceId` echoes it, so the caller owns the correlation key. `traceparent`
    still starts the server span; it does not supply this id.

    `message_id` is required on the response, so the transport mints one when the
    caller omits it.
    """

    return TurnContext(
        trace_id=body.context.transaction_id,
        session_id=body.context.session_id,
        transaction_id=body.context.transaction_id,
        message_id=body.context.message_id or message_id,
    )


def _current_index(thread: list[schema.InputMessage]) -> int:
    """The last user message is the ask; everything before it is history.

    Whether a trailing assistant message can exist at all is the caller's
    business — the mapping only has to find the question.
    """

    for i in reversed(range(len(thread))):
        if thread[i].role == "user":
            return i
    raise ValueError("message.input carries no user message to act on")


def _text(message: schema.InputMessage) -> str:
    return " ".join(part.text for part in message.content).strip()


def _user_id(entries: list[schema.Identity]) -> str:
    """The first identity entry names the actor. None present means the turn is
    unattributed, which the contract spells "anonymous"."""

    return next((e.user_id for e in entries), ANONYMOUS)


def _location(wire: schema.Location | None) -> Location | None:
    if wire is None:
        return None
    return Location(region=wire.region, area=wire.area, geometry=_point(wire.geometry))


def _point(wire: schema.Geometry | None) -> Point | None:
    """GeoJSON is `[longitude, latitude]`. This is the only place that ordering
    is positional; everything inward uses named axes."""

    if wire is None:
        return None
    lon, lat = wire.coordinates
    return Point(lon=lon, lat=lat)


UNAVAILABLE_MESSAGE = "A service this turn needed could not be reached."


def to_created_frame(
    ctx: TurnContext, *, now: datetime, seq: int | None, response_id: str
) -> schema.TurnResponse:
    return schema.TurnResponse(
        context=_response_context(ctx, now=now, seq=seq, response_id=response_id),
        message=schema.ResponseMessage(),
    )


def to_claim_frame(
    claim: Claim,
    ctx: TurnContext,
    *,
    now: datetime,
    seq: int | None,
    response_id: str,
) -> schema.TurnResponse:
    return schema.TurnResponse(
        context=_response_context(ctx, now=now, seq=seq, response_id=response_id),
        message=schema.ResponseMessage(content=[_block(claim.content)]),
    )


def to_terminal_frame(
    finished: TurnFinished,
    ctx: TurnContext,
    *,
    now: datetime,
    seq: int | None,
    response_id: str,
) -> schema.TurnResponse:
    return schema.TurnResponse(
        context=_response_context(ctx, now=now, seq=seq, response_id=response_id),
        message=schema.ResponseMessage(
            outcome=schema.Outcome(
                status=finished.outcome.status.value,
                confidence=finished.outcome.confidence,
                cause=finished.outcome.cause.value if finished.outcome.cause else None,
            ),
            content=[_block(b) for b in finished.content],
            sources=[
                schema.Source(id=s.id, name=s.name, kind=s.kind.value, url=s.url)
                for s in finished.sources
            ],
            error=_error(finished.outcome),
        ),
    )


def _response_context(
    ctx: TurnContext, *, now: datetime, seq: int | None, response_id: str
) -> schema.ResponseContext:
    return schema.ResponseContext(
        version=DSS_RELEASE,
        timestamp=now,
        message_id=ctx.message_id,
        session_id=ctx.session_id,
        trace_id=ctx.trace_id,
        res_message_id=response_id,
        sequence_number=seq,
    )


def _error(outcome: TurnOutcome) -> schema.TurnError | None:
    """A dependency failure is reported twice on purpose — as `outcome.cause`,
    and as a structured `error` a caller can branch on."""

    if outcome.status is not TurnStatus.UNAVAILABLE:
        return None
    cause = outcome.cause.value if outcome.cause else Cause.INTERNAL.value
    return schema.TurnError(
        code=cause,
        message=UNAVAILABLE_MESSAGE,
        retryable=True,
        retry_after_seconds=outcome.retry_after_seconds,
    )


def _block(content: OutputContent) -> schema.OutputText | schema.OutputRefusal:
    if isinstance(content, RefusalBlock):
        return schema.OutputRefusal(text=content.text)
    return schema.OutputText(text=content.text, source_ids=list(content.source_ids))
