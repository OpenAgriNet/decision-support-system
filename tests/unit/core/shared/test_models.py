from __future__ import annotations

import pytest

from dss.core.shared.language import ENGLISH
from dss.core.shared.models import (
    ConversationMessage,
    FormContext,
    FormReference,
    TextQuery,
    TurnInput,
    UserTurn,
)


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


def test_form_contexts_with_the_same_type_are_equal() -> None:
    """A registry keys on the context, so two of the same type must collide."""
    one = FormContext(scheme="PMFBY", category="Grievance", action="support")
    other = FormContext(scheme="PMFBY", category="Grievance", action="support")

    assert one == other
    assert len({one, other}) == 1


def test_history_message_can_carry_a_form_reference() -> None:
    context = FormContext(scheme="PMFBY", category="Grievance", action="support")

    message = ConversationMessage(
        role="assistant",
        text="Please fill this in.",
        form=FormReference(form_id="8f3c", context=context),
    )

    assert message.text == "Please fill this in."
    assert message.form == FormReference(form_id="8f3c", context=context)


def test_submission_message_has_no_words() -> None:
    context = FormContext(scheme="PMFBY", category="Grievance", action="support")
    reference = FormReference(form_id="8f3c", context=context, submission_id="s1")

    message = ConversationMessage(role="user", form=reference)

    assert message.text == ""
