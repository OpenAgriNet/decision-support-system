from __future__ import annotations

from dss.core.forms.models import (
    FieldDefinition,
    FieldKind,
    FieldRules,
    FormBlock,
    FormContext,
    FormDefinition,
    Option,
)
from dss.core.shared.models import Claim, TurnFinished, TurnOutcome, TurnStatus

_PMFBY_GRIEVANCE = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PHONE = FieldDefinition(
    name="applicantPhone",
    label="Phone used at enrolment",
    kind=FieldKind.TEXT,
    rules=FieldRules(required=True, pattern=r"^[6-9][0-9]{9}$"),
)
_GRIEVANCE_FORM = FormDefinition(context=_PMFBY_GRIEVANCE, fields=(_PHONE,))


def test_form_contexts_with_the_same_type_are_equal() -> None:
    """A registry keys on the context, so two of the same type must collide."""
    other = FormContext(scheme="PMFBY", category="Grievance", action="support")

    assert other == _PMFBY_GRIEVANCE
    assert len({_PMFBY_GRIEVANCE, other}) == 1


def test_form_block_is_a_reply_block() -> None:
    block = FormBlock(form_id="8f3c", form=_GRIEVANCE_FORM)

    claim = Claim(content=block)

    assert claim.content == block


def test_finished_turn_serialises_a_form_block() -> None:
    """Content is typed by the base block, so without SerializeAsAny a form
    would dump as an empty object."""
    block = FormBlock(form_id="8f3c", form=_GRIEVANCE_FORM)
    outcome = TurnOutcome(status=TurnStatus.REQUIRES_INPUT, confidence=80)

    dumped = TurnFinished(outcome=outcome, content=(block,)).model_dump()

    assert dumped["content"][0]["form"]["fields"][0]["name"] == "applicantPhone"


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


def test_form_definition_finds_a_field_by_name() -> None:
    assert _GRIEVANCE_FORM.field("applicantPhone") == _PHONE
    assert _GRIEVANCE_FORM.field("cropYear") is None
