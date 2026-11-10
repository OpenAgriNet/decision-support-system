from __future__ import annotations

import pytest

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.forms.init_flow import (
    end_for_session_not_kept,
    FlowSettled,
    InitCall,
    InitNotConfigured,
    NoInitChallenge,
    end_for_failed_init,
    find_init_call,
    find_init_failure,
    outcome_of_failed_call,
    settle_sent_init,
)
from dss.core.forms.models import (
    ActionSchema,
    FailedCallOutcome,
    FieldValue,
    FormBlock,
    FormContext,
    FormDefinition,
    FormEntry,
    FormErrorBlock,
    FormRecords,
    FormStatus,
    FormSubmission,
    ValidSubmission,
)
from dss.core.forms.service import validate_submission
from dss.core.intent.models import Intent
from dss.core.planner.models import NO_READABLE_REPLY, Failure
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.shared.models import Cause, TextBlock, TurnStatus
from tests.support.grievance_pack import (
    PMKISAN_INIT,
    PMKISAN_SUPPORT,
    build_grievance_forms,
)
from tests.support.providers import grievance_provider

_TEXT = load_clarification_text()
_FORMS = build_grievance_forms()
_PROVIDER = grievance_provider("kisan-provider", "PM-KISAN")
_INTENT = Intent(asks=(), confidence=1.0)
_KISAN_VALUES = {
    "enrolmentId": "PMK1234567",
    "grievance.category.code": "G001",
    "grievance.description": "My last installment has not come.",
}
_KISAN_LOOKUP = {"enrolmentId": "PMK1234567", "case.filedOn": "2026-10-29"}


def _submission(
    context: FormContext, values: dict[str, str], *, form_id: str = "offer-1"
) -> FormSubmission:
    return FormSubmission(
        form_id=form_id,
        context=context,
        submission_id="submission-1",
        values=tuple(
            FieldValue(name=name, value=value) for name, value in values.items()
        ),
    )


def _valid(submission: FormSubmission) -> ValidSubmission:
    checked = validate_submission(submission, _FORMS)
    assert isinstance(checked, ValidSubmission)
    return checked


def _failure(
    status_code: int | None, *, retry_after_seconds: int | None = None
) -> Failure:
    # retryable says a 404 is worth retrying; the init rules read the status alone.
    return Failure(
        ask_index=0,
        capability="openagrinet:Grievance",
        reason="call failed",
        retryable=True,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
    )


def _reply(attributes: dict) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id="kisan-provider",
        provider_name="KISAN-PROVIDER",
        capability="openagrinet:Grievance",
        resource_id="res:kisan-provider:grievance",
        attributes=attributes,
        validity=None,
    )


_DETAILS = _submission(PMKISAN_SUPPORT, _KISAN_VALUES)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, FailedCallOutcome.REFUSED),
        (404, FailedCallOutcome.REFUSED),
        (422, FailedCallOutcome.REFUSED),
        (408, FailedCallOutcome.NOT_SENT),
        (429, FailedCallOutcome.NOT_SENT),
        (None, FailedCallOutcome.NOT_SENT),
        (500, FailedCallOutcome.OUTCOME_UNKNOWN),
        (503, FailedCallOutcome.OUTCOME_UNKNOWN),
        (NO_READABLE_REPLY, FailedCallOutcome.OUTCOME_UNKNOWN),
    ],
)
def test_a_failure_is_read_from_its_status_code_alone(
    status_code: int | None, expected: FailedCallOutcome
) -> None:
    assert outcome_of_failed_call(_failure(status_code)) is expected


def test_a_details_form_needing_init_finds_the_init_call_and_form() -> None:
    init = find_init_call(_valid(_DETAILS), _FORMS)

    assert isinstance(init, InitCall)
    assert init.action.context == PMKISAN_INIT
    assert init.init_form == _FORMS.find(PMKISAN_INIT)


class _WithoutInit:
    def find(self, context: FormContext) -> FormDefinition | None:
        return _FORMS.find(context)

    def action_schema(self, context: FormContext) -> ActionSchema | None:
        if context == PMKISAN_INIT:
            return None
        return _FORMS.action_schema(context)


def test_an_init_the_pack_does_not_give_is_not_configured() -> None:
    assert isinstance(
        find_init_call(_valid(_DETAILS), _WithoutInit()), InitNotConfigured
    )


def test_details_without_a_value_init_sends_are_not_configured() -> None:
    details = _valid(_DETAILS).model_copy(
        update={"values": {"grievance.description": "x"}}
    )

    assert isinstance(find_init_call(details, _FORMS), InitNotConfigured)


def test_an_init_reply_with_a_challenge_is_no_failure() -> None:
    reply = _reply({"challenge": {"issued": {"method": "OTP"}}})

    assert find_init_failure(reply) is None


@pytest.mark.parametrize(
    "attributes",
    [{}, {"challenge": {}}, {"challenge": "OTP"}, {"enrolmentId": "x"}],
)
def test_an_init_reply_without_a_challenge_is_a_failure(attributes: dict) -> None:
    assert isinstance(find_init_failure(_reply(attributes)), NoInitChallenge)


def test_a_failed_init_call_is_its_own_failure() -> None:
    failure = _failure(503)

    assert find_init_failure(failure) is failure


def _details_handed_back(text: str) -> tuple[TextBlock, FormErrorBlock]:
    return (
        TextBlock(text=text),
        FormErrorBlock(
            form_id="offer-1", context=PMKISAN_SUPPORT, submission_id="submission-1"
        ),
    )


@pytest.mark.parametrize(
    ("status_code", "cause", "text"),
    [
        (503, Cause.PROVIDER_UNAVAILABLE, _TEXT.init_not_sent),
        (NO_READABLE_REPLY, Cause.PROVIDER_UNAVAILABLE, _TEXT.init_not_sent),
        (408, Cause.PROVIDER_UNAVAILABLE, _TEXT.init_not_sent),
        (429, Cause.PROVIDER_UNAVAILABLE, _TEXT.init_not_sent),
        (None, Cause.PROVIDER_UNAVAILABLE, _TEXT.init_not_sent),
        (400, Cause.PROVIDER_REJECTED, _TEXT.init_rejected),
        (404, Cause.PROVIDER_REJECTED, _TEXT.init_rejected),
        (422, Cause.PROVIDER_REJECTED, _TEXT.init_rejected),
    ],
    ids=["E8-5xx", "E8-unreadable", "E8-408", "E8-429", "E8-unsent"]
    + ["E9-400", "E9-404", "E9-422"],
)
def test_a_failed_init_hands_the_details_form_back(
    status_code: int | None, cause: Cause, text: str
) -> None:
    ending = end_for_failed_init(
        _failure(status_code, retry_after_seconds=30), details=_DETAILS, text=_TEXT
    )

    assert ending.status is TurnStatus.UNAVAILABLE
    assert ending.cause is cause
    assert ending.retry_after_seconds == 30
    assert ending.answer.content == _details_handed_back(text)


def test_an_init_reply_without_a_challenge_reads_as_refused() -> None:
    ending = end_for_failed_init(NoInitChallenge(), details=_DETAILS, text=_TEXT)

    assert ending.status is TurnStatus.UNAVAILABLE
    assert ending.cause is Cause.PROVIDER_REJECTED
    assert ending.retry_after_seconds is None
    assert ending.answer.content == _details_handed_back(_TEXT.init_rejected)


@pytest.mark.parametrize(
    "failure",
    [InitNotConfigured(), _failure(401), _failure(403)],
    ids=["E11-not-configured", "E17a-401", "E17a-403"],
)
def test_an_init_failing_on_our_side_ends_text_only(
    failure: InitNotConfigured | Failure,
) -> None:
    ending = end_for_failed_init(failure, details=_DETAILS, text=_TEXT)

    assert ending.status is TurnStatus.UNAVAILABLE
    assert ending.cause is Cause.INTERNAL
    assert ending.answer.content == (TextBlock(text=_TEXT.went_wrong),)


def test_a_session_that_is_not_kept_ends_before_init() -> None:
    ending = end_for_session_not_kept(_TEXT)

    assert ending.status is TurnStatus.UNAVAILABLE
    assert ending.cause is Cause.INTERNAL
    assert ending.answer.content == (TextBlock(text=_TEXT.form_cannot_complete),)


def _waiting(details: FormSubmission = _DETAILS) -> FormEntry:
    definition = _FORMS.find(details.context)
    assert definition is not None
    return FormEntry(
        definition=definition,
        submission=details,
        status=FormStatus.AWAITING_INIT,
        provider=_PROVIDER,
    )


def _sent_init(forms: FormRecords) -> FlowSettled:
    init = find_init_call(_valid(_DETAILS), _FORMS)
    assert isinstance(init, InitCall)
    return settle_sent_init(
        forms,
        _valid(_DETAILS),
        _DETAILS,
        provider=_PROVIDER,
        init=init,
        text=_TEXT,
    )


def test_a_sent_init_leaves_the_details_waiting_with_their_provider() -> None:
    done = _waiting().model_copy(update={"status": FormStatus.DONE})

    settled = _sent_init({"filed": done})

    assert settled.forms == {"filed": done, "offer-1": _waiting()}


def test_a_sent_init_asks_for_the_init_form() -> None:
    settled = _sent_init({})

    assert settled.ending.status is TurnStatus.REQUIRES_INPUT
    assert settled.ending.cause is None
    told, offered = settled.ending.answer.content
    assert told == TextBlock(text=_TEXT.init_response_needed)
    assert isinstance(offered, FormBlock)
    assert offered.form == _FORMS.find(PMKISAN_INIT)
