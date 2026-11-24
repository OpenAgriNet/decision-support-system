from __future__ import annotations

import pytest

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.channel.models import ComposedAnswer
from dss.core.channel.service import answer_for_unserved_asks
from dss.core.forms.models import (
    FieldError,
    FieldErrorCode,
    FieldErrorEntry,
    FlowEnding,
    FormBlock,
    FormDefinition,
    FormErrorBlock,
    FormErrors,
    FormSubmission,
    UnknownForm,
)
from dss.core.forms.service import (
    answer_for_form_errors,
    answer_for_init_form,
    answer_for_unknown_form,
    answer_with_text,
    end_for_already_filed,
    end_for_failed_call,
    end_for_invalid_form,
    end_for_no_provider,
    end_for_unknown_form,
    form_error_block,
    offer_form,
)
from dss.core.intent.models import InteractionType, SubjectCategory
from dss.core.planner.models import Failure
from dss.core.shared.models import (
    Cause,
    ProviderAction,
    ProviderFailureKind,
    TextBlock,
    TurnStatus,
)
from tests.support.asks import an_ask
from tests.support.grievance_pack import (
    PMFBY_SUPPORT,
    PMKISAN_INIT,
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


def _failure(
    kind: ProviderFailureKind, *, retry_after_seconds: int | None = None
) -> Failure:
    return Failure(
        ask_id=0,
        capability="openagrinet:Grievance",
        kind=kind,
        retry_after_seconds=retry_after_seconds,
    )


def _form_handed_back(text: str) -> ComposedAnswer:
    return ComposedAnswer(
        content=(
            TextBlock(text=text),
            FormErrorBlock(
                form_id="offer-1", context=PMFBY_SUPPORT, submission_id="submission-1"
            ),
        )
    )


def _end_for(
    action: ProviderAction,
    kind: ProviderFailureKind,
    *,
    retry_after_seconds: int | None = None,
) -> FlowEnding:
    return end_for_failed_call(
        _SUBMISSION,
        _failure(kind, retry_after_seconds=retry_after_seconds),
        _TEXT,
        action=action,
    )


@pytest.mark.parametrize(
    ("action", "text"),
    [
        (ProviderAction.SUPPORT, _TEXT.not_filed_rejected),
        (ProviderAction.STATUS, _TEXT.status_not_found),
        (ProviderAction.INIT, _TEXT.init_rejected),
    ],
)
def test_a_refused_call_is_rejected(action: ProviderAction, text: str) -> None:
    ending = _end_for(action, ProviderFailureKind.REFUSED)

    assert ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.PROVIDER_REJECTED,
        answer=_form_handed_back(text),
    )


@pytest.mark.parametrize(
    ("action", "text"),
    [
        (ProviderAction.SUPPORT, _TEXT.not_filed),
        (ProviderAction.STATUS, _TEXT.status_not_checked),
        (ProviderAction.INIT, _TEXT.init_not_sent),
    ],
)
def test_a_call_not_sent_is_try_later(action: ProviderAction, text: str) -> None:
    ending = _end_for(action, ProviderFailureKind.NOT_SENT, retry_after_seconds=30)

    assert ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.PROVIDER_UNAVAILABLE,
        answer=_form_handed_back(text),
        retry_after_seconds=30,
    )


def test_a_filing_that_may_have_gone_through_hands_no_form_back() -> None:
    ending = _end_for(
        ProviderAction.SUPPORT,
        ProviderFailureKind.OUTCOME_UNKNOWN,
        retry_after_seconds=30,
    )

    assert ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.PROVIDER_OUTCOME_UNKNOWN,
        answer=answer_with_text(_TEXT.outcome_unknown),
    )


@pytest.mark.parametrize(
    ("action", "text"),
    [
        (ProviderAction.STATUS, _TEXT.status_not_checked),
        (ProviderAction.INIT, _TEXT.init_not_sent),
    ],
)
def test_a_read_that_may_have_gone_through_hands_the_form_back(
    action: ProviderAction, text: str
) -> None:
    ending = _end_for(action, ProviderFailureKind.OUTCOME_UNKNOWN)

    assert ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.PROVIDER_UNAVAILABLE,
        answer=_form_handed_back(text),
    )


@pytest.mark.parametrize("action", list(ProviderAction))
def test_an_internal_error_says_went_wrong(action: ProviderAction) -> None:
    ending = _end_for(action, ProviderFailureKind.UNAUTHORISED)

    assert ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.INTERNAL,
        answer=answer_with_text(_TEXT.went_wrong),
    )


def test_an_answer_with_text_is_that_text_alone() -> None:
    answer = answer_with_text("This form was already sent.")

    assert answer.content == (TextBlock(text="This form was already sent."),)


def test_the_init_form_goes_out_as_a_new_offer_under_its_text() -> None:
    init_form = build_grievance_forms().find(PMKISAN_INIT)
    assert init_form is not None

    told, offered = answer_for_init_form(init_form, "Enter the password.").content

    assert told == TextBlock(text="Enter the password.")
    assert isinstance(offered, FormBlock)
    assert offered.form == init_form
    assert offered.form_id


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
    ask = an_ask(
        agriculture_subjects="PMFBY",
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
    )

    assert end_for_no_provider(ask, _TEXT) == FlowEnding(
        status=TurnStatus.NO_MATCH,
        cause=None,
        answer=answer_for_unserved_asks((ask,), _TEXT),
    )
