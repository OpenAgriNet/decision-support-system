"""Wire <-> domain. Pure functions — the one module that speaks both vocabularies."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from uuid import uuid4

from dss.adapters.http.v1 import schema
from dss.core.forms.models import (
    FieldDefinition,
    FieldValue,
    FormBlock,
    FormContext,
    FormErrorBlock,
    FormReference,
    FormSubmission,
)
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import (
    Cause,
    Claim,
    ClaimDelta,
    ConversationMessage,
    Geometry,
    Location,
    OutputBlock,
    RefusalBlock,
    Source,
    TextBlock,
    TextQuery,
    TurnContext,
    TurnFinished,
    TurnInput,
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

    `senderId` rides on the request context (ADR-0020): it names the
    experience-layer application calling, and selects whose provider ranking
    applies. Blank counts as absent — a proxy that fills the field with an
    empty string has not named anybody — and absent is served the default
    ranking, never refused.
    """

    thread = body.message.input
    current = _current_index(thread)
    attributes = body.message.attributes
    response = attributes.response

    return UserTurn(
        input=_input_of(thread[current]),
        session_id=body.context.session_id,
        transaction_id=(
            body.context.transaction_id
            if body.context.transaction_id is not None
            else str(uuid4())
        ),
        source_lang=LanguageTag(attributes.source_language),
        target_lang=LanguageTag(attributes.target_language),
        channel=attributes.channel,
        user=_user(body.message.user_context),
        history=[_history_message(m) for m in thread[:current]],
        location=_location(attributes.location),
        response_max_chars=response.max_characters if response else None,
        sender_id=(body.context.sender_id or "").strip() or None,
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


def _input_of(message: schema.InputMessage) -> TurnInput:
    """What the farmer sent: their words, or one filled-in form. One kind per
    message, so a turn runs on one path."""

    kinds = {type(part) for part in message.content}
    if len(kinds) > 1:
        raise ValueError("a message carries one kind of content: text, or one form")
    first = message.content[0]
    to_input = _INPUT_FOR.get(type(first))
    if to_input is None:
        raise ValueError(f"a {first.type} item is not something the farmer sends")
    return to_input(message.content)


def _text_query(parts: Sequence[schema.InputContent]) -> TextQuery:
    return TextQuery(text=_joined_text(parts))


def _submission(parts: Sequence[schema.InputContent]) -> FormSubmission:
    match parts:
        case [schema.FormContent(submission_id=None)]:
            raise ValueError("a submitted form needs a submissionId")
        case [schema.FormContent(submission_id=str() as submission_id) as form]:
            return FormSubmission(
                form_id=form.form_id,
                context=_form_context(form.context),
                submission_id=submission_id,
                values=tuple(_field_value(f) for f in form.fields),
            )
    raise ValueError("a message carries one form")


def _field_value(field: schema.InputFormField) -> FieldValue:
    if field.value is None:
        raise ValueError(f"the submitted field {field.name!r} needs a value")
    return FieldValue(name=field.name, value=field.value)


def _history_message(message: schema.InputMessage) -> ConversationMessage:
    """A prior message's words, and a reference to the form it carried, if
    any. The form's fields are never read from history: the DSS holds no
    values between turns."""

    reference = next(
        (
            FormReference(
                form_id=part.form_id,
                context=_form_context(part.context),
                submission_id=part.submission_id,
                item=part.type,
            )
            for part in message.content
            if isinstance(part, schema.FormContent | schema.FormErrorContent)
        ),
        None,
    )
    return ConversationMessage(
        role=message.role, text=_joined_text(message.content), reference=reference
    )


# How each kind of item becomes the turn's input. A kind absent here is not
# something the farmer sends, such as the DSS's own form_error reply.
_INPUT_FOR: Mapping[type, Callable[[Sequence[schema.InputContent]], TurnInput]] = {
    schema.TextContent: _text_query,
    schema.FormContent: _submission,
}


def _form_context(wire: schema.InputFormContext) -> FormContext:
    return FormContext(scheme=wire.scheme, category=wire.category, action=wire.action)


def _joined_text(parts: Sequence[schema.InputContent]) -> str:
    return " ".join(
        part.text for part in parts if isinstance(part, schema.TextContent)
    ).strip()


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


def to_turn_context(
    body: schema.TurnRequest, *, message_id: str, network_sender_id: str
) -> TurnContext:
    """Assemble the ids a response is filed under.

    The trace id **is** the caller's `transactionId` — the contract says
    `traceId` echoes it, so the caller owns the correlation key. `traceparent`
    still starts the server span; it does not supply this id.

    `messageId` is required on the response, so the transport mints one when the
    caller omits it.
    """

    return TurnContext(
        trace_id=body.context.transaction_id or str(uuid4()),
        message_id=body.context.message_id or message_id,
        session_id=body.context.session_id,
        sender_id=_caller_or_host(body.context.sender_id, network_sender_id),
    )


def _caller_or_host(sender_id: str | None, network_sender_id: str) -> str:
    return sender_id if sender_id and sender_id.strip() else network_sender_id


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
        message=schema.ResponseMessage(content=[_block(claim.content, claim.sources)]),
    )


def to_delta_frame(
    delta: ClaimDelta,
    ctx: TurnContext,
    *,
    now: datetime,
    seq: int | None,
    response_id: str,
) -> schema.TurnResponse:
    """One piece of the answer, on its way out while the rest is still being
    written. No sources and no annotations — see `schema.OutputTextDelta`."""

    return schema.TurnResponse(
        context=_response_context(ctx, now=now, seq=seq, response_id=response_id),
        message=schema.ResponseMessage(
            content=[schema.OutputTextDelta(text=delta.text)]
        ),
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
            content=[_block(b, finished.sources) for b in finished.content],
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
        # A provider that refused a filing would refuse it again unchanged.
        retryable=outcome.cause is not Cause.PROVIDER_REJECTED,
        retry_after_seconds=outcome.retry_after_seconds,
    )


def _block(
    content: OutputBlock, sources: Sequence[Source]
) -> (
    schema.OutputText
    | schema.OutputRefusal
    | schema.OutputForm
    | schema.OutputFormError
):
    match content:
        case RefusalBlock():
            return schema.OutputRefusal(text=content.text)
        case TextBlock():
            return schema.OutputText(
                text=content.text, annotations=_annotations(content, sources)
            )
        case FormBlock():
            return _form(content)
        case FormErrorBlock():
            return _form_error(content)
    raise TypeError(f"no wire shape for a {type(content).__name__}")


def _form(block: FormBlock) -> schema.OutputForm:
    context = block.form.context
    return schema.OutputForm(
        form_id=block.form_id,
        context=schema.OutputFormContext(
            scheme=context.scheme, category=context.category, action=context.action
        ),
        fields=[_form_field(field) for field in block.form.fields],
    )


def _form_error(block: FormErrorBlock) -> schema.OutputFormError:
    return schema.OutputFormError(
        form_id=block.form_id,
        context=schema.OutputFormContext(
            scheme=block.context.scheme,
            category=block.context.category,
            action=block.context.action,
        ),
        submission_id=block.submission_id,
        fields=[
            schema.OutputFormErrorField(name=f.name, label=f.label, error=f.error)
            for f in block.fields
        ],
    )


def _form_field(field: FieldDefinition) -> schema.OutputFormField:
    rules = field.rules
    return schema.OutputFormField(
        name=field.name,
        label=field.label,
        kind=field.kind.value,
        required=rules.required,
        pattern=rules.pattern,
        min_length=rules.min_length,
        max_length=rules.max_length,
        options=[
            schema.OutputFormOption(value=option.value, label=option.label)
            for option in rules.options
        ],
    )


def _annotations(
    block: TextBlock, sources: Sequence[Source]
) -> list[schema.Annotation]:
    """Render a block's citations as contract annotations.

    A block cites its sources as a whole, so each annotation spans the whole
    block: `startIndex 0`, `endIndex len(text)`. That is what "this sentence came
    from that source" means, and it satisfies the contract's required offsets
    without inventing sub-sentence spans nothing has computed.

    When real sub-span citations arrive, `TextBlock` grows an annotations field
    and this stops synthesising.

    `sourceName` and `url` are denormalised onto each citation so a streamed
    claim names its source before `message.sources` arrives. An id that
    resolves to nothing still cites: the bare id shows the defect, where a
    dropped annotation would hide the claim's provenance with it.
    """

    by_id = {source.id: source for source in sources}
    end = len(block.text)  # code points — see schema.Annotation
    annotations = []
    for source_id in block.source_ids:
        cited = by_id.get(source_id)
        annotations.append(
            schema.Annotation(
                source_id=source_id,
                start_index=0,
                end_index=end,
                source_name=cited.name if cited else None,
                url=cited.url if cited else None,
            )
        )
    return annotations
