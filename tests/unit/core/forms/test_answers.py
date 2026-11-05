from __future__ import annotations

import pytest

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.forms.models import (
    FieldError,
    FieldErrorCode,
    FieldErrorEntry,
    FormContext,
    FormDefinition,
    FormErrorBlock,
    FormErrors,
    FormSubmission,
)
from dss.core.forms.service import (
    answer_for_failed_call,
    cause_for_failed_call,
    form_error_block,
    offer_form,
)
from dss.core.planner.models import Failure
from dss.core.shared.models import Cause, TextBlock
from tests.support.grievance_pack import (
    PMFBY_SUPPORT,
    build_grievance_forms,
)


def test_each_offer_gets_its_own_id() -> None:
    form = FormDefinition(context=PMFBY_SUPPORT, fields=())

    first, second = offer_form(form), offer_form(form)

    assert first.form == second.form == form
    assert first.form_id and first.form_id != second.form_id


def test_an_error_block_names_each_bad_field_with_its_label_and_message() -> None:
    definition = build_grievance_forms().find(PMFBY_SUPPORT)
    assert definition is not None
    errors = FormErrors(
        form_id="offer-1",
        context=PMFBY_SUPPORT,
        submission_id="submission-1",
        definition=definition,
        field_errors=(
            FieldError(name="applicantMobile", code=FieldErrorCode.PATTERN),
            FieldError(name="enrolmentId", code=FieldErrorCode.MIN_LENGTH),
        ),
    )

    block = form_error_block(errors, load_clarification_text())

    assert block == FormErrorBlock(
        form_id="offer-1",
        context=PMFBY_SUPPORT,
        submission_id="submission-1",
        fields=(
            FieldErrorEntry(
                name="applicantMobile",
                label="Applicant mobile",
                error="Applicant mobile is not in the right format.",
            ),
            FieldErrorEntry(
                name="enrolmentId",
                label="Enrolment id",
                error="Enrolment id must be at least 10 characters.",
            ),
        ),
    )


def test_a_field_off_the_form_has_no_label_and_is_named_in_the_message() -> None:
    definition = build_grievance_forms().find(PMFBY_SUPPORT)
    assert definition is not None
    errors = FormErrors(
        form_id="offer-1",
        context=PMFBY_SUPPORT,
        submission_id="submission-1",
        definition=definition,
        field_errors=(FieldError(name="village", code=FieldErrorCode.UNKNOWN_FIELD),),
    )

    block = form_error_block(errors, load_clarification_text())

    assert block.fields == (
        FieldErrorEntry(
            name="village", label="", error="village is not part of this form."
        ),
    )


@pytest.mark.parametrize(
    ("is_retryable", "cause"),
    [(True, Cause.PROVIDER_UNAVAILABLE), (False, Cause.PROVIDER_REJECTED)],
)
def test_a_failed_call_is_unavailable_only_if_trying_again_may_work(
    is_retryable: bool, cause: Cause
) -> None:
    failure = Failure(
        ask_index=0,
        capability="openagrinet:Grievance",
        reason="-",
        retryable=is_retryable,
    )

    assert cause_for_failed_call(failure) is cause


_TEXT = load_clarification_text()


@pytest.mark.parametrize(
    ("action", "cause", "expected_text"),
    [
        ("support", Cause.PROVIDER_UNAVAILABLE, _TEXT.not_filed),
        ("support", Cause.PROVIDER_REJECTED, _TEXT.not_filed_rejected),
        ("status", Cause.PROVIDER_UNAVAILABLE, _TEXT.status_not_checked),
        ("status", Cause.PROVIDER_REJECTED, _TEXT.status_not_found),
    ],
)
def test_a_failed_call_is_told_in_the_words_for_its_action_and_cause(
    action: str, cause: Cause, expected_text: str
) -> None:
    submission = FormSubmission(
        form_id="offer-1",
        context=FormContext(scheme="PMFBY", category="Grievance", action=action),
        submission_id="submission-1",
        values=(),
    )

    told, kept = answer_for_failed_call(submission, cause, _TEXT).content

    assert told == TextBlock(text=expected_text)
    assert kept == FormErrorBlock(
        form_id="offer-1", context=submission.context, submission_id="submission-1"
    )
