"""The paths the happy-path tests never reach."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dss.adapters.http.v1 import mapping, schema
from dss.core.shared.models import (
    Claim,
    RefusalBlock,
    TurnContext,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
)

NOW = datetime(2026, 9, 7, 8, 0, tzinfo=UTC)
CTX = TurnContext(trace_id="trc_1", session_id="conv_1", message_id="msg_in")


def test_a_turn_with_no_location_maps_to_none(a_body):
    body = a_body()
    body["message"]["attributes"].pop("location")

    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(body))

    assert turn.location is None


def test_a_location_without_a_point_keeps_its_region_and_area(a_body):
    """Two of the three deployments cannot supply coordinates, so a location with
    only a region has to survive the mapping."""

    body = a_body()
    body["message"]["attributes"]["location"].pop("geometry")

    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(body))

    assert turn.location is not None
    assert (turn.location.region, turn.location.area) == ("IN-GJ", "Anand")
    assert turn.location.geometry is None


def test_a_refusal_block_becomes_a_refusal_content_item():
    """A refusal carries no citations — there is nothing to cite when the DSS
    declines to answer."""

    finished = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.REJECTED, confidence=98),
        content=(RefusalBlock(text="I can only answer agriculture questions."),),
    )

    frame = mapping.to_terminal_frame(
        finished, CTX, now=NOW, seq=None, response_id="msg_1"
    )

    item = frame.message.content[0]
    assert isinstance(item, schema.OutputRefusal)
    assert item.type == "refusal"
    assert not hasattr(item, "source_ids")


def test_a_claim_frame_renders_a_refusal_too():
    frame = mapping.to_claim_frame(
        Claim(content=RefusalBlock(text="No.")),
        CTX,
        now=NOW,
        seq=1,
        response_id="msg_1",
    )

    assert frame.message.content[0].type == "refusal"


def test_a_thread_with_no_user_message_is_rejected(a_body):
    """The schema allows it — `input` only requires one entry, and an assistant
    message is a valid entry. The mapping is where it has to be caught."""

    body = a_body(
        message__input=[
            {"role": "assistant", "content": [{"type": "text", "text": "Hello?"}]}
        ]
    )

    with pytest.raises(ValueError, match="no user message"):
        mapping.to_user_turn(schema.TurnRequest.model_validate(body))
