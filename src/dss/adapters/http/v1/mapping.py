"""Wire <-> domain. Pure functions — the one module that speaks both vocabularies."""

from __future__ import annotations

from datetime import datetime

from dss.adapters.http.v1 import schema
from dss.core.shared.models import (
    Cause,
    Claim,
    ConversationMessage,
    Geometry,
    Location,
    OutputContent,
    RefusalBlock,
    TextBlock,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
    UserDetails,
    UserTurn,
)

# The concrete DSS release that handled the turn. Distinct from the envelope
# version: the contract shape and the build that served it move independently.
DSS_RELEASE = "v1.0.0"


def to_user_turn(body: schema.TurnRequest) -> UserTurn:
    """Normalize a validated request body into the turn the core works with.

    `enriched_query` mirrors `original_query`: enrichment has not landed, and
    `core/shared/models.py` defines both so later slices need not re-thread the
    second field through every signature.
    """

    thread = body.message.input
    current = _current_index(thread)
    attributes = body.message.attributes
    response = attributes.response
    query = _text(thread[current])

    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id=body.context.session_id,
        transaction_id = (
    body.context.transaction_id
    if body.context.transaction_id is not None
    else str(uuid4())
),
        source_lang=attributes.source_language,
        target_lang=attributes.target_language,
        channel=attributes.channel,
        user=_user(body.message.user_context),
        history=[
            ConversationMessage(role=m.role, text=_text(m)) for m in thread[:current]
        ],
        location=_location(attributes.location),
        response_max_chars=response.max_characters if response else None,
    )


def _user(entries: list[schema.Identity]) -> UserDetails:
    """The first identity entry names the actor. None present leaves the turn
    unattributed — `user_id` stays `None` rather than being invented."""

    user_id = next((e.user_id for e in entries), None)
    return UserDetails(user_id=user_id)


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


def _location(wire: schema.Location | None) -> Location | None:
    if wire is None:
        return None
    return Location(
        region=wire.region, area=wire.area, geometry=_geometry(wire.geometry)
    )


def _geometry(wire: schema.Geometry | None) -> Geometry | None:
    """GeoJSON is `[longitude, latitude]` — the reverse of what the current
    deployments send.

    The domain `Geometry` keeps the pair positional (it mirrors GeoJSON), so
    unlike a named-axes type it cannot make the reversal unrepresentable. A range
    check would not catch it either: for Anand, 72.93 and 22.56 are both a valid
    latitude *and* a valid longitude. The mapping test is the guard.
    """

    if wire is None:
        return None
    return Geometry(coordinates=list(wire.coordinates))


def to_turn_context(body: schema.TurnRequest, *, message_id: str) -> TurnContext:
    """Assemble the ids a response is filed under.

    The trace id **is** the caller's `transactionId` — the contract says
    `traceId` echoes it, so the caller owns the correlation key. `traceparent`
    still starts the server span; it does not supply this id.

    `messageId` is required on the response, so the transport mints one when the
    caller omits it.
    """

    return TurnContext(
        trace_id=body.context.transaction_id,
        message_id=body.context.message_id or message_id,
        session_id=body.context.session_id,
    )


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
        transaction_id=ctx.trace_id,
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
    return schema.OutputText(text=content.text, annotations=_annotations(content))


def _annotations(block: TextBlock) -> list[schema.Annotation]:
    """Render a block's citations as contract annotations.

    A block cites its sources as a whole, so each annotation spans the whole
    block: `startIndex 0`, `endIndex len(text)`. That is what "this sentence came
    from that source" means, and it satisfies the contract's required offsets
    without inventing sub-sentence spans nothing has computed.

    When real sub-span citations arrive, `TextBlock` grows an annotations field
    and this stops synthesising.
    """

    end = len(block.text)  # code points — see schema.Annotation
    return [
        schema.Annotation(source_id=source_id, start_index=0, end_index=end)
        for source_id in block.source_ids
    ]
