from __future__ import annotations

from collections.abc import Mapping

from dss.core.forms.models import (
    FieldError,
    FieldErrorCode,
    FieldValue,
    FormContext,
    FormErrors,
    FormSubmission,
    UnknownForm,
    ValidSubmission,
)
from dss.core.forms.service import (
    WITHHELD,
    free_text_of,
    validate_submission,
    withhold_values,
)
from tests.support.grievance_pack import (
    PMFBY_SUPPORT,
    build_grievance_forms,
)

_PMFBY_VALUES = {
    "enrolmentId": "PMFBY-2024-000123",
    "applicantMobile": "9876543210",
    "cropYear": "2024",
    "season": "Kharif",
    "grievance.description": "Claim for the Kharif crop loss has not been paid.",
}


def _submission(values: Mapping[str, str], *, context: FormContext = PMFBY_SUPPORT):
    return FormSubmission(
        form_id="offer-1",
        context=context,
        submission_id="submission-1",
        values=tuple(
            FieldValue(name=name, value=value) for name, value in values.items()
        ),
    )


def test_a_phone_off_the_pattern_is_the_only_error() -> None:
    submission = _submission({**_PMFBY_VALUES, "applicantMobile": "12345"})

    outcome = validate_submission(submission, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="applicantMobile", code=FieldErrorCode.PATTERN),
    )


def test_a_field_sent_twice_is_a_duplicate() -> None:
    submission = _submission(_PMFBY_VALUES)
    twice = submission.model_copy(
        update={"values": (*submission.values, FieldValue(name="season", value="Rabi"))}
    )

    outcome = validate_submission(twice, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="season", code=FieldErrorCode.DUPLICATE),
    )


def test_a_context_nobody_serves_is_an_unknown_form() -> None:
    unserved = FormContext(scheme="NO-SUCH", category="Grievance", action="support")

    outcome = validate_submission(
        _submission(_PMFBY_VALUES, context=unserved), build_grievance_forms()
    )

    assert outcome == UnknownForm(context=unserved)


def test_a_missing_required_field_is_reported_as_required() -> None:
    without_year = {k: v for k, v in _PMFBY_VALUES.items() if k != "cropYear"}

    outcome = validate_submission(_submission(without_year), build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="cropYear", code=FieldErrorCode.REQUIRED),
    )


def test_a_value_under_the_minimum_length_is_too_short() -> None:
    submission = _submission({**_PMFBY_VALUES, "enrolmentId": "PMFBY-1"})

    outcome = validate_submission(submission, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="enrolmentId", code=FieldErrorCode.MIN_LENGTH),
    )


def test_a_value_over_the_maximum_length_is_too_long() -> None:
    submission = _submission({**_PMFBY_VALUES, "enrolmentId": "PMFBY-" + "9" * 30})

    outcome = validate_submission(submission, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="enrolmentId", code=FieldErrorCode.MAX_LENGTH),
    )


def test_a_value_outside_the_options_is_not_an_option() -> None:
    submission = _submission({**_PMFBY_VALUES, "season": "Monsoon"})

    outcome = validate_submission(submission, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="season", code=FieldErrorCode.NOT_AN_OPTION),
    )


def test_a_field_the_form_does_not_have_is_unknown() -> None:
    submission = _submission({**_PMFBY_VALUES, "village": "Anand"})

    outcome = validate_submission(submission, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="village", code=FieldErrorCode.UNKNOWN_FIELD),
    )


def test_withholding_keeps_the_field_names_and_drops_every_value() -> None:
    submission = _submission({"applicantMobile": "9876543210", "season": "Kharif"})

    withheld = withhold_values(submission)

    assert withheld.values == (
        FieldValue(name="applicantMobile", value=WITHHELD),
        FieldValue(name="season", value=WITHHELD),
    )
    assert (withheld.form_id, withheld.context, withheld.submission_id) == (
        submission.form_id,
        submission.context,
        submission.submission_id,
    )
    assert "9876543210" not in withheld.model_dump_json()


def test_an_unknown_field_sent_twice_is_listed_once() -> None:
    submission = _submission(_PMFBY_VALUES)
    twice = submission.model_copy(
        update={
            "values": (
                *submission.values,
                FieldValue(name="village", value="Anand"),
                FieldValue(name="village", value="Nadiad"),
            )
        }
    )

    outcome = validate_submission(twice, build_grievance_forms())

    assert isinstance(outcome, FormErrors)
    assert outcome.field_errors == (
        FieldError(name="village", code=FieldErrorCode.DUPLICATE),
    )


def test_the_free_text_of_a_submission_is_its_freetext_fields_only() -> None:
    valid = validate_submission(_submission(_PMFBY_VALUES), build_grievance_forms())
    assert isinstance(valid, ValidSubmission)

    assert free_text_of(valid) == (_PMFBY_VALUES["grievance.description"],)
