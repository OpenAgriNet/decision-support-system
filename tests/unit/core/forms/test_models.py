from __future__ import annotations

import pytest

from dss.core.forms.models import (
    ActionField,
    ActionSchema,
    FieldDefinition,
    FieldKind,
    FieldRules,
    FieldSource,
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


def _farmer(path: str, **rules: object) -> ActionField:
    return ActionField(path=path, source=FieldSource.FARMER, **rules)


def _schema(*fields: ActionField) -> ActionSchema:
    return ActionSchema(context=_PMFBY_GRIEVANCE, must_send=fields)


def test_form_keeps_only_the_farmer_fields_in_order() -> None:
    schema = _schema(
        ActionField(
            path="informationMode",
            source=FieldSource.FIXED,
            allowed_values=("OnDemand",),
        ),
        ActionField(path="provider.id", source=FieldSource.PROVIDER),
        _farmer("enrolmentId"),
        _farmer("cropYear"),
    )

    form = schema.form()

    assert form.context == _PMFBY_GRIEVANCE
    assert [field.name for field in form.fields] == ["enrolmentId", "cropYear"]


def test_form_field_carries_the_pack_rules_and_is_required() -> None:
    form = _schema(
        _farmer("enrolmentId", pattern="^[A-Z0-9]+$", min_length=10, max_length=30)
    ).form()

    assert form.fields[0].rules == FieldRules(
        required=True, pattern="^[A-Z0-9]+$", min_length=10, max_length=30
    )


def test_a_field_with_allowed_values_is_a_select_of_them() -> None:
    form = _schema(_farmer("season", allowed_values=("Kharif", "Rabi"))).form()

    season = form.fields[0]

    assert season.kind is FieldKind.SELECT
    assert season.rules.options == (
        Option(value="Kharif", label="Kharif"),
        Option(value="Rabi", label="Rabi"),
    )


def test_an_option_that_is_not_text_is_written_as_json() -> None:
    form = _schema(_farmer("isUrgent", allowed_values=(True, False))).form()

    assert [option.value for option in form.fields[0].rules.options] == [
        "true",
        "false",
    ]


@pytest.mark.parametrize(
    ("max_length", "kind"),
    [
        (2000, FieldKind.TEXTAREA),
        (256, FieldKind.TEXTAREA),
        (255, FieldKind.TEXT),
        (30, FieldKind.TEXT),
        (None, FieldKind.TEXT),
    ],
)
def test_a_long_answer_is_a_textarea(max_length: int | None, kind: FieldKind) -> None:
    form = _schema(_farmer("value", max_length=max_length)).form()

    assert form.fields[0].kind is kind


def test_options_make_a_select_however_long_the_answer() -> None:
    form = _schema(_farmer("value", allowed_values=("a",), max_length=2000)).form()

    assert form.fields[0].kind is FieldKind.SELECT


@pytest.mark.parametrize(
    ("path", "label"),
    [
        ("cropYear", "Crop year"),
        ("applicantMobile", "Applicant mobile"),
        ("grievance.description", "Grievance description"),
        ("case.ticketNo", "Case ticket no"),
        ("aadhaarOTP", "Aadhaar OTP"),
        ("PMKISANId", "PMKISAN id"),
        ("use2FA", "Use 2 FA"),
    ],
)
def test_the_label_is_the_path_in_words(path: str, label: str) -> None:
    assert _schema(_farmer(path)).form().fields[0].label == label
