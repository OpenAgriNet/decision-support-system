from __future__ import annotations

import pytest

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.channel.service import answer_for_unserved_asks
from dss.core.forms.models import (
    FieldError,
    FieldErrorCode,
    FieldErrorEntry,
    FlowEnding,
    FormContext,
    FormDefinition,
    FormErrorBlock,
    FormErrors,
    FormSubmission,
    UnknownForm,
)
from dss.core.forms.service import (
    answer_for_failed_call,
    answer_for_form_errors,
    answer_for_unknown_form,
    answer_with_text,
    end_for_already_filed,
    end_for_failed_call,
    end_for_invalid_form,
    end_for_no_init_pending,
    end_for_no_provider,
    end_for_unknown_form,
    form_error_block,
    offer_form,
)
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.planner.models import Failure
from dss.core.shared.models import Cause, TextBlock, TurnStatus
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


_TEXT = load_clarification_text()

_SUBMISSION = FormSubmission(
    form_id="offer-1",
    context=PMFBY_SUPPORT,
    submission_id="submission-1",
    values=(),
)


@pytest.mark.parametrize(
    ("is_retryable", "cause"),
    [(True, Cause.PROVIDER_UNAVAILABLE), (False, Cause.PROVIDER_REJECTED)],
)
def test_a_failed_call_is_rejected_unless_trying_again_may_work(
    is_retryable: bool, cause: Cause
) -> None:
    failure = Failure(
        ask_index=0,
        capability="openagrinet:Grievance",
        reason="-",
        retryable=is_retryable,
        retry_after_seconds=30,
    )

    ending = end_for_failed_call(_SUBMISSION, failure, _TEXT)

    assert ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=cause,
        answer=answer_for_failed_call(_SUBMISSION, cause, _TEXT),
        retry_after_seconds=30,
    )


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


def test_an_answer_with_text_is_that_text_alone() -> None:
    answer = answer_with_text("This form was already sent.")

    assert answer.content == (TextBlock(text="This form was already sent."),)


@pytest.mark.parametrize(
    ("ending", "expected"),
    [
        (
            end_for_already_filed(_TEXT),
            FlowEnding(
                status=TurnStatus.REJECTED,
                cause=Cause.ALREADY_FILED,
                answer=answer_with_text(_TEXT.already_filed),
            ),
        ),
        (
            end_for_unknown_form(_TEXT),
            FlowEnding(
                status=TurnStatus.REQUIRES_INPUT,
                cause=Cause.INVALID_INPUT,
                answer=answer_for_unknown_form(_TEXT),
            ),
        ),
        (
            end_for_no_init_pending(_TEXT),
            FlowEnding(
                status=TurnStatus.REQUIRES_INPUT,
                cause=Cause.NO_INIT_PENDING,
                answer=answer_with_text(_TEXT.no_init_pending),
            ),
        ),
    ],
)
def test_a_flow_ended_before_any_call_has_its_status_cause_and_text(
    ending: FlowEnding, expected: FlowEnding
) -> None:
    assert ending == expected


def test_a_form_with_field_errors_is_handed_back_with_them() -> None:
    definition = build_grievance_forms().find(PMFBY_SUPPORT)
    assert definition is not None
    errors = FormErrors(
        form_id="offer-1",
        context=PMFBY_SUPPORT,
        submission_id="submission-1",
        definition=definition,
        field_errors=(FieldError(name="enrolmentId", code=FieldErrorCode.REQUIRED),),
    )

    assert end_for_invalid_form(errors, _TEXT) == FlowEnding(
        status=TurnStatus.REQUIRES_INPUT,
        cause=Cause.INVALID_INPUT,
        answer=answer_for_form_errors(errors, _TEXT),
    )


def test_a_form_nobody_serves_ends_as_an_unknown_form() -> None:
    unknown = UnknownForm(context=PMFBY_SUPPORT)

    assert end_for_invalid_form(unknown, _TEXT) == end_for_unknown_form(_TEXT)


def test_a_form_with_no_provider_ends_no_match_naming_its_ask() -> None:
    intent = Intent(
        asks=(
            Ask(
                agriculture_subjects="PMFBY",
                subject_categories=SubjectCategory.GRIEVANCE,
                interaction_type=InteractionType.ACT,
            ),
        ),
        confidence=1.0,
    )

    assert end_for_no_provider(intent, _TEXT) == FlowEnding(
        status=TurnStatus.NO_MATCH,
        cause=None,
        answer=answer_for_unserved_asks(intent.asks, _TEXT),
    )
