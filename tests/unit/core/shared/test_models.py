from __future__ import annotations

import pytest

from dss.core.shared.language import ENGLISH
from dss.core.shared.models import (
    Claim,
    ConversationMessage,
    FieldDefinition,
    FieldKind,
    FieldRules,
    FormBlock,
    FormContext,
    FormReference,
    Option,
    TextQuery,
    TurnInput,
    UserTurn,
)

_PMFBY_GRIEVANCE = FormContext(scheme="PMFBY", category="Grievance", action="support")


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
    reference = FormReference(form_id="8f3c", context=_PMFBY_GRIEVANCE)

    message = ConversationMessage(
        role="assistant", text="Please fill this in.", form=reference
    )

    assert message.text == "Please fill this in."
    assert message.form == reference


def test_submission_message_has_no_words() -> None:
    reference = FormReference(
        form_id="8f3c", context=_PMFBY_GRIEVANCE, submission_id="s1"
    )

    message = ConversationMessage(role="user", form=reference)

    assert message.text == ""


def test_form_block_is_output_content() -> None:
    phone = FieldDefinition(
        name="applicantPhone",
        label="Phone used at enrolment",
        kind=FieldKind.TEXT,
        rules=FieldRules(required=True, pattern=r"^[6-9][0-9]{9}$"),
    )
    block = FormBlock(form_id="8f3c", context=_PMFBY_GRIEVANCE, fields=(phone,))

    claim = Claim(content=block)

    assert claim.content == block


def test_select_field_carries_its_options() -> None:
    seasons = (
        Option(value="Kharif", label="Kharif"),
        Option(value="Rabi", label="Rabi"),
    )

    season = FieldDefinition(
        name="season",
        label="Season",
        kind=FieldKind.SELECT,
        rules=FieldRules(required=True, options=seasons),
    )

    assert season.rules.options == seasons
