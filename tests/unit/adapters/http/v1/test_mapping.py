"""Wire <-> domain translation. Pure functions, no server, no doubles."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.adapters.http.v1 import mapping, schema
from dss.core.shared.models import Channel, HistoryEntry, Point, Role


def test_geometry_coordinates_are_longitude_then_latitude(a_body):
    """GeoJSON order is [lon, lat] — the reverse of what the deployments send.
    Mapping is the only place the pair is positional, so it is the only place
    this can go wrong."""

    body = schema.TurnRequest.model_validate(a_body())

    turn = mapping.to_user_turn(body)

    assert turn.location is not None
    assert turn.location.geometry == Point(lon=72.93, lat=22.56)


def test_identity_supplies_the_user_id(a_body):
    body = schema.TurnRequest.model_validate(a_body())

    assert mapping.to_user_turn(body).user_id == "usr_9921"


def test_user_id_is_anonymous_when_no_identity_is_supplied(a_body, omit):
    """The contract's `user_id` is "anonymous" if unknown — a turn need not be
    attributed, and the core must never see an empty string it has to interpret."""

    body = schema.TurnRequest.model_validate(a_body(message__userContext=omit))

    assert mapping.to_user_turn(body).user_id == "anonymous"


def _thread() -> list[dict]:
    return [
        {"role": "user", "content": [{"type": "text", "text": "Wheat price?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Rs 2275."}]},
        {"role": "user", "content": [{"type": "text", "text": "And potato?"}]},
    ]


def test_the_last_user_message_is_the_current_query(a_body):
    body = schema.TurnRequest.model_validate(a_body(message__input=_thread()))

    assert mapping.to_user_turn(body).query == "And potato?"


def test_messages_before_the_current_query_become_history(a_body):
    body = schema.TurnRequest.model_validate(a_body(message__input=_thread()))

    assert mapping.to_user_turn(body).history == (
        HistoryEntry(role=Role.USER, content="Wheat price?"),
        HistoryEntry(role=Role.ASSISTANT, content="Rs 2275."),
    )


def test_several_content_parts_join_into_one_query(a_body):
    """The contract lets one message carry several typed parts. The core reads a
    single question, so the parts are joined here rather than downstream."""

    body = schema.TurnRequest.model_validate(
        a_body(
            message__input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What disease is this?"},
                        {"type": "text", "text": "The leaves are curling."},
                    ],
                }
            ]
        )
    )

    assert (
        mapping.to_user_turn(body).query
        == "What disease is this? The leaves are curling."
    )


def test_languages_and_channel_reach_the_turn(a_body):
    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(a_body()))

    assert (turn.source_lang, turn.target_lang) == ("hi", "hi")
    assert turn.channel is Channel.WEB


def test_the_response_cap_reaches_the_turn(a_body):
    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(a_body()))

    assert turn.response_max_chars == 1200


def test_no_response_block_means_no_cap(a_body, omit):
    body = a_body()
    body["message"]["attributes"].pop("response")

    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(body))

    assert turn.response_max_chars is None


def test_the_context_carries_the_session_and_the_callers_transaction_id(a_body):
    body = a_body()
    body["context"]["transactionId"] = "txn_77"

    ctx = mapping.to_turn_context(
        schema.TurnRequest.model_validate(body), message_id="minted"
    )

    assert (ctx.session_id, ctx.transaction_id) == ("conv_8f3a1c", "txn_77")


def test_the_trace_id_comes_from_the_caller_span_not_the_body(a_body):
    """`traceparent` is the source (contract §1.1). A body field would let a
    caller forge the audit key, so the wire model has no place to put one."""

    body = a_body()
    body["context"]["traceId"] = "forged"

    with pytest.raises(ValidationError):
        schema.TurnRequest.model_validate(body)
