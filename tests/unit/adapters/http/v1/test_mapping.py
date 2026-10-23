"""Wire <-> domain translation. Pure functions, no server, no doubles."""

from __future__ import annotations

import pytest

from dss.adapters.http.v1 import mapping, schema
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import ConversationMessage, Geometry, TurnContext


def test_geometry_coordinates_are_longitude_then_latitude(a_body):
    """GeoJSON order is [lon, lat] — the reverse of what the deployments send.
    Mapping is the only place the pair is positional, so it is the only place
    this can go wrong."""

    body = schema.TurnRequest.model_validate(a_body())

    turn = mapping.to_user_turn(body)

    assert turn.location is not None
    assert turn.location.geometry == Geometry(coordinates=[72.93, 22.56])


def test_identity_supplies_the_user_id(a_body):
    body = schema.TurnRequest.model_validate(a_body())

    assert mapping.to_user_turn(body).user.user_id == "usr_9921"


def test_the_turn_is_unattributed_when_no_identity_is_supplied(a_body, omit):
    """A turn need not be attributed. `UserDetails.user_id` stays `None` rather
    than being invented — the wire's "anonymous" is a presentation choice, not a
    domain fact."""

    body = schema.TurnRequest.model_validate(a_body(message__userContext=omit))

    assert mapping.to_user_turn(body).user.user_id is None


def _thread() -> list[dict]:
    return [
        {"role": "user", "content": [{"type": "text", "text": "Wheat price?"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Rs 2275."}]},
        {"role": "user", "content": [{"type": "text", "text": "And potato?"}]},
    ]


def test_the_last_user_message_is_the_current_query(a_body):
    body = schema.TurnRequest.model_validate(a_body(message__input=_thread()))

    assert mapping.to_user_turn(body).input.text == "And potato?"


def test_messages_before_the_current_query_become_history(a_body):
    body = schema.TurnRequest.model_validate(a_body(message__input=_thread()))

    assert mapping.to_user_turn(body).history == [
        ConversationMessage(role="user", text="Wheat price?"),
        ConversationMessage(role="assistant", text="Rs 2275."),
    ]


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
        mapping.to_user_turn(body).input.text
        == "What disease is this? The leaves are curling."
    )


def test_languages_and_channel_reach_the_turn(a_body):
    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(a_body()))

    assert (turn.source_lang, turn.target_lang) == (
        LanguageTag("hi"),
        LanguageTag("hi"),
    )
    assert turn.channel == "web"


def test_a_language_that_is_not_a_tag_is_refused(a_body):
    body = schema.TurnRequest.model_validate(
        a_body(message__attributes__targetLanguage="gujarati")
    )
    with pytest.raises(ValueError):
        mapping.to_user_turn(body)


def test_the_response_cap_reaches_the_turn(a_body):
    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(a_body()))

    assert turn.response_max_chars == 1200


def test_no_response_block_means_no_cap(a_body, omit):
    body = a_body()
    body["message"]["attributes"].pop("response")

    turn = mapping.to_user_turn(schema.TurnRequest.model_validate(body))

    assert turn.response_max_chars is None


NETWORK_SENDER_ID = "consumer.oan.dev"


def _context_from(body: dict) -> TurnContext:
    return mapping.to_turn_context(
        schema.TurnRequest.model_validate(body),
        message_id="minted",
        network_sender_id=NETWORK_SENDER_ID,
    )


def test_the_context_carries_the_session_and_the_callers_transaction_id(a_body):
    body = a_body()
    body["context"]["transactionId"] = "txn_77"

    validated = schema.TurnRequest.model_validate(body)
    ctx = _context_from(body)

    assert ctx.session_id == "conv_8f3a1c"
    # `transaction_id` is not duplicated on the context — `trace_id` already is
    # it. The turn keeps its own copy for the Provider hop.
    assert ctx.trace_id == "txn_77"
    assert mapping.to_user_turn(validated).transaction_id == "txn_77"


def test_a_body_trace_id_is_ignored(a_body):
    """The trace id is the caller's `transactionId`; a `traceId` field cannot
    replace it."""

    body = a_body()
    body["context"]["traceId"] = "forged"

    assert _context_from(body).trace_id == body["context"]["transactionId"]


def test_the_sender_id_is_kept(a_body):
    body = a_body()
    body["context"]["senderId"] = "bap.example.org"

    assert _context_from(body).sender_id == "bap.example.org"


def test_a_missing_sender_id_falls_back(a_body):
    body = a_body()
    body["context"].pop("senderId", None)

    assert _context_from(body).sender_id == NETWORK_SENDER_ID


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_sender_id_falls_back(a_body, blank):
    body = a_body()
    body["context"]["senderId"] = blank

    assert _context_from(body).sender_id == NETWORK_SENDER_ID


def test_the_sender_id_is_carried_onto_the_turn(a_body):
    """It rides the request context (ADR-0020), so the mapping reads it off
    the body like every other field."""

    body = schema.TurnRequest.model_validate(
        a_body(context__senderId="kisan-mitra-web")
    )

    assert mapping.to_user_turn(body).sender_id == "kisan-mitra-web"


def test_a_turn_from_a_caller_that_named_nobody_carries_no_sender(a_body):
    """Every caller that predates the field. The default ranking serves it."""

    body = schema.TurnRequest.model_validate(a_body())

    assert mapping.to_user_turn(body).sender_id is None
