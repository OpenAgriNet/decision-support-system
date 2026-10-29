"""Wire <-> domain translation. Pure functions, no server, no doubles."""

from __future__ import annotations

import pytest

from dss.adapters.http.v1 import mapping, schema
from dss.core.forms.models import (
    FieldValue,
    FormContext,
    FormReference,
    FormSubmission,
)
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


def _form_item(**overrides) -> dict:
    item = {
        "type": "form",
        "formId": "offer-1",
        "context": {"scheme": "PMFBY", "category": "Grievance", "action": "support"},
        "submissionId": "submission-1",
        "fields": [
            {"name": "applicantMobile", "value": "9876543210"},
            {"name": "season", "value": "Kharif"},
        ],
    }
    return {**item, **overrides}


def test_a_form_item_in_the_current_message_is_a_submission(a_body):
    thread = [{"role": "user", "content": [_form_item()]}]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    turn = mapping.to_user_turn(body)

    assert turn.input == FormSubmission(
        form_id="offer-1",
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        submission_id="submission-1",
        values=(
            FieldValue(name="applicantMobile", value="9876543210"),
            FieldValue(name="season", value="Kharif"),
        ),
    )


def test_a_form_in_history_is_a_reference_without_its_fields(a_body):
    """The DSS holds no values between turns: a submitted form in the history
    names the form and the submission, nothing the farmer typed."""

    thread = [
        {"role": "user", "content": [_form_item()]},
        {"role": "user", "content": [{"type": "text", "text": "Was it filed?"}]},
    ]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    (earlier,) = mapping.to_user_turn(body).history

    assert earlier == ConversationMessage(
        role="user",
        text="",
        reference=FormReference(
            form_id="offer-1",
            context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
            submission_id="submission-1",
        ),
    )
    assert "9876543210" not in earlier.model_dump_json()


def test_a_form_error_in_history_is_a_reference_too(a_body):
    error_item = {
        "type": "form_error",
        "formId": "offer-1",
        "context": {"scheme": "PMFBY", "category": "Grievance", "action": "support"},
        "submissionId": "submission-1",
        "fields": [{"name": "applicantMobile", "label": "Mobile", "error": "bad"}],
    }
    thread = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Check."}, error_item],
        },
        {"role": "user", "content": [_form_item(submissionId="submission-2")]},
    ]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    (earlier,) = mapping.to_user_turn(body).history

    assert earlier.text == "Check."
    assert isinstance(earlier.reference, FormReference)
    assert earlier.reference.submission_id == "submission-1"
    assert earlier.reference.item == "form_error"


def test_text_and_a_form_in_one_message_is_refused(a_body):
    thread = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "here"}, _form_item()],
        }
    ]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    with pytest.raises(ValueError, match="one kind"):
        mapping.to_user_turn(body)


def test_a_submitted_form_needs_a_submission_id(a_body):
    thread = [{"role": "user", "content": [_form_item(submissionId=None)]}]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    with pytest.raises(ValueError, match="submissionId"):
        mapping.to_user_turn(body)


def test_an_echoed_form_offer_in_history_is_accepted(a_body):
    """A UI sends the DSS's own offer back as history; its fields are
    definitions, not values, and are not read."""

    offer = {
        "type": "form",
        "formId": "offer-1",
        "context": {"scheme": "PMFBY", "category": "Grievance", "action": "support"},
        "fields": [
            {
                "name": "applicantMobile",
                "label": "Applicant mobile",
                "kind": "text",
                "required": True,
                "pattern": "^[6-9][0-9]{9}$",
                "options": [],
            }
        ],
    }
    thread = [
        {"role": "assistant", "content": [{"type": "text", "text": "Fill in."}, offer]},
        {"role": "user", "content": [_form_item()]},
    ]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    (earlier,) = mapping.to_user_turn(body).history

    assert isinstance(earlier.reference, FormReference)
    assert earlier.reference.submission_id is None


def test_a_submitted_field_without_a_value_is_refused(a_body):
    item = _form_item(fields=[{"name": "applicantMobile", "label": "Applicant mobile"}])
    thread = [{"role": "user", "content": [item]}]
    body = schema.TurnRequest.model_validate(a_body(message__input=thread))

    with pytest.raises(ValueError, match="value"):
        mapping.to_user_turn(body)
