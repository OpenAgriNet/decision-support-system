from __future__ import annotations

import pytest

from dss.core.forms.models import FormContext, FormReference
from dss.core.shared.language import ENGLISH
from dss.core.shared.models import ConversationMessage, TextQuery, TurnInput, UserTurn


@pytest.mark.parametrize(
    ("target_lang", "expected"), [("en", True), ("en-IN", True), ("gu", False)]
)
def test_is_target_lang_within_a_broader_tag(
    a_turn, target_lang: str, expected: bool
) -> None:
    assert a_turn(target_lang=target_lang).is_target_lang_within(ENGLISH) is expected


def test_turn_carries_its_text_as_input(a_turn) -> None:
    turn = a_turn(input=TextQuery(text="q"))

    assert turn.input.text == "q"


def test_turn_serialises_its_input_fields(a_turn) -> None:
    """A turn typed by the base input would otherwise dump an empty object."""
    envelope = a_turn().model_dump(exclude={"input"})
    turn = UserTurn[TurnInput](input=TextQuery(text="q"), **envelope)

    assert turn.model_dump()["input"] == {"text": "q"}


def test_a_form_message_reads_as_one_fixed_line_by_role() -> None:
    """No prompt may print a form's fields or values: a model reads only
    that a form was offered or submitted, and which side did it."""

    reference = FormReference(
        form_id="offer-1",
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        submission_id="submission-1",
    )
    offered = ConversationMessage(
        role="assistant", text="Please fill in the form.", reference=reference
    )
    submitted = ConversationMessage(role="user", text="", reference=reference)
    plain = ConversationMessage(role="user", text="Wheat price?")

    assert offered.prompt_text == "Please fill in the form. (a form was offered)"
    assert submitted.prompt_text == "a form was submitted"
    assert plain.prompt_text == "Wheat price?"


def test_a_form_handed_back_is_not_an_offer() -> None:
    """The DSS's `form_error` reply echoes in history as an assistant item;
    reading it as an offer would hide the rejection from the model."""

    handed_back = FormReference(
        form_id="offer-1",
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        submission_id="submission-1",
        item="form_error",
    )
    message = ConversationMessage(role="assistant", text="", reference=handed_back)

    assert message.prompt_text == "a form was handed back with errors"
