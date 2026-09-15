"""Domain events -> wire frames. Pure: the clock and the minted id are arguments,
so every assertion is deterministic."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from dss.adapters.http.v1 import mapping
from dss.core.shared.models import (
    Cause,
    Claim,
    Source,
    SourceKind,
    TextBlock,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
)

NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
RESPONSE_ID = "msg_out_1"


@pytest.fixture
def ctx():
    return TurnContext(
        trace_id="trc_9f2b",
        session_id="conv_8f3a1c",
        message_id="msg_in_1",
    )


@pytest.fixture
def answered():
    block = TextBlock(text="Wheat is Rs 2,275 per quintal.", source_ids=("src_1",))
    return TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(block,),
        sources=(Source(id="src_1", name="Agmarknet", kind=SourceKind.PROVIDER),),
    )


def test_the_callers_message_id_is_echoed_and_the_response_gets_its_own(ctx, answered):
    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.context.message_id == "msg_in_1"
    assert frame.context.res_message_id == RESPONSE_ID


def test_every_frame_carries_the_trace_id_in_its_body(ctx, answered):
    """A header is lost the moment the caller stores the answer, and the audit
    trail needs the join key to survive that."""

    frames = [
        mapping.to_created_frame(ctx, now=NOW, seq=2, response_id=RESPONSE_ID),
        mapping.to_claim_frame(
            Claim(content=answered.content[0]),
            ctx,
            now=NOW,
            seq=1,
            response_id=RESPONSE_ID,
        ),
        mapping.to_terminal_frame(
            answered, ctx, now=NOW, seq=3, response_id=RESPONSE_ID
        ),
    ]

    assert [f.context.trace_id for f in frames] == ["trc_9f2b"] * 3


def test_the_terminal_frame_carries_the_outcome_content_and_sources(ctx, answered):
    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.message.outcome is not None
    assert frame.message.outcome.status == "answered"
    assert frame.message.outcome.cause is None
    assert [b.text for b in frame.message.content] == ["Wheat is Rs 2,275 per quintal."]
    assert [a.source_id for b in frame.message.content for a in b.annotations] == [
        "src_1"
    ]
    assert [s.name for s in frame.message.sources] == ["Agmarknet"]


def test_a_cause_serializes_to_its_wire_string(ctx):
    finished = TurnFinished(
        outcome=TurnOutcome(
            status=TurnStatus.REJECTED, confidence=98, cause=Cause.UNSAFE_ILLEGAL
        ),
        content=(),
    )

    frame = mapping.to_terminal_frame(
        finished, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.message.outcome is not None
    assert frame.message.outcome.cause == "unsafe_illegal"


def test_a_claim_frame_carries_one_block_and_no_outcome(ctx, answered):
    """A claim is a whole sentence, finished when sent. The outcome is not known
    until the turn ends, so a claim frame must not imply one."""

    frame = mapping.to_claim_frame(
        Claim(content=answered.content[0]),
        ctx,
        now=NOW,
        seq=1,
        response_id=RESPONSE_ID,
    )

    assert frame.message.outcome is None
    assert len(frame.message.content) == 1


def test_the_created_frame_announces_the_turn_and_carries_no_content(ctx):
    frame = mapping.to_created_frame(ctx, now=NOW, seq=1, response_id=RESPONSE_ID)

    assert frame.message.outcome is None
    assert frame.message.content == []
    assert frame.context.sequence_number == 1


def test_a_non_streaming_frame_has_no_sequence_number(ctx, answered):
    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.context.sequence_number is None


def test_a_dependency_failure_carries_a_structured_error(ctx):
    """`outcome.cause` and `message.error.code` say the same thing on purpose —
    the contract carries both, so a caller can branch on either."""

    finished = TurnFinished(
        outcome=TurnOutcome(
            status=TurnStatus.UNAVAILABLE,
            cause=Cause.PROVIDER_UNAVAILABLE,
            confidence=0,
            retry_after_seconds=30,
        ),
        content=(),
    )

    frame = mapping.to_terminal_frame(
        finished, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.message.error is not None
    assert frame.message.error.code == "provider_unavailable"
    assert frame.message.error.retryable is True
    assert frame.message.error.retry_after_seconds == 30


def test_an_answered_turn_carries_no_error_object(ctx, answered):
    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.message.error is None


def test_the_cause_is_sent_as_null_rather_than_omitted(ctx, answered):
    """`cause` is required and nullable. Frames are dumped with
    `exclude_none=True`, which would drop it — and did, unnoticed, through
    several hand-written conformance tests."""

    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    rendered = json.loads(frame.model_dump_json(by_alias=True, exclude_none=True))
    assert rendered["message"]["outcome"]["cause"] is None


def test_a_citation_spans_the_whole_block(ctx, answered):
    """A block cites its sources as a whole, so the annotation covers the whole
    block. Offsets are code points — the contract requires them and names no
    unit (see schema.Annotation)."""

    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    block = frame.message.content[0]
    annotation = block.annotations[0]
    assert (annotation.start_index, annotation.end_index) == (0, len(block.text))


def test_a_block_citing_nothing_carries_no_annotations(ctx):
    finished = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text="A linking sentence."),),
    )

    frame = mapping.to_terminal_frame(
        finished, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.message.content[0].annotations == []


def test_the_offsets_are_code_points_not_bytes(ctx):
    """Devanagari is three bytes per character in UTF-8. A byte offset would
    land every citation past the end of the sentence."""

    text = "गेहूं का भाव"
    finished = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text=text, source_ids=("src_1",)),),
    )

    frame = mapping.to_terminal_frame(
        finished, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    annotation = frame.message.content[0].annotations[0]
    assert annotation.end_index == len(text)
    assert annotation.end_index < len(text.encode("utf-8"))


def test_an_annotation_names_the_source_it_cites(ctx, answered):
    """`sourceName` and `url` are denormalised onto the citation so a caller
    reads it without joining, and so a streamed claim can name its source
    before `message.sources` arrives in the terminal frame."""

    frame = mapping.to_terminal_frame(
        answered, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    annotation = frame.message.content[0].annotations[0]
    assert annotation.source_id == "src_1"
    assert annotation.source_name == "Agmarknet"


def test_an_annotation_carries_the_sources_url(ctx):
    finished = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text="Rs 2,275.", source_ids=("src_1",)),),
        sources=(
            Source(
                id="src_1",
                name="Agmarknet",
                kind=SourceKind.PROVIDER,
                url="https://agmarknet.gov.in",
            ),
        ),
    )

    frame = mapping.to_terminal_frame(
        finished, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    assert frame.message.content[0].annotations[0].url == "https://agmarknet.gov.in"


def test_a_claim_frame_names_its_source_too(ctx, answered):
    """The claim arrives before the terminal frame, so a caller cannot join on
    `sourceId` yet — the name has to travel with the claim."""

    frame = mapping.to_claim_frame(
        Claim(content=answered.content[0], sources=answered.sources),
        ctx,
        now=NOW,
        seq=1,
        response_id=RESPONSE_ID,
    )

    assert frame.message.content[0].annotations[0].source_name == "Agmarknet"


def test_a_claim_with_no_sources_still_cites_by_id(ctx, answered):
    frame = mapping.to_claim_frame(
        Claim(content=answered.content[0]),
        ctx,
        now=NOW,
        seq=1,
        response_id=RESPONSE_ID,
    )

    annotation = frame.message.content[0].annotations[0]
    assert annotation.source_id == "src_1"
    assert annotation.source_name is None


def test_an_unresolved_source_id_keeps_the_citation_unnamed(ctx):
    """A block citing an id no source carries is a defect, but dropping the
    citation would hide the claim's provenance marker as well as the bug."""

    finished = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=(TextBlock(text="Rs 2,275.", source_ids=("src_9",)),),
        sources=(Source(id="src_1", name="Agmarknet", kind=SourceKind.PROVIDER),),
    )

    frame = mapping.to_terminal_frame(
        finished, ctx, now=NOW, seq=None, response_id=RESPONSE_ID
    )

    annotation = frame.message.content[0].annotations[0]
    assert annotation.source_id == "src_9"
    assert (annotation.source_name, annotation.url) == (None, None)
