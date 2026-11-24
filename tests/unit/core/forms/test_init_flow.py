from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.forms.challenge import (
    ChallengeMethod,
    IssuedChallenge,
    UnknownChallenge,
)
from dss.core.forms.init_flow import (
    FlowDecision,
    InitCall,
    InitNotConfigured,
    InitReplyWithoutChallenge,
    NoInitPending,
    SavedDetailsUnusable,
    WaitingFlow,
    decide_after_failed_init_response,
    decide_after_sent_init,
    decide_with_no_waiting_flow,
    end_for_failed_init,
    end_for_session_not_kept,
    find_init_call,
    find_waiting_flow,
    outcome_of_failed_call,
    read_init_reply,
    with_details_form_done,
)
from dss.core.forms.models import (
    ActionSchema,
    FailedCallOutcome,
    FieldErrorEntry,
    FieldValue,
    FlowEnding,
    FormAwaitingInit,
    FormBlock,
    FormContext,
    FormDefinition,
    FormDone,
    FormErrorBlock,
    FormRecords,
    FormSubmission,
    ValidSubmission,
)
from dss.core.forms.service import validate_submission
from dss.core.intent.models import Ask
from dss.core.planner.models import NO_READABLE_REPLY, Failure
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.shared.models import Cause, TextBlock, TurnStatus
from tests.support.asks import an_ask
from tests.support.grievance_pack import (
    PMKISAN_INIT,
    PMKISAN_STATUS,
    PMKISAN_SUPPORT,
    build_grievance_forms,
)
from tests.support.providers import grievance_provider

_TEXT = load_clarification_text()
_FORMS = build_grievance_forms()
_AWAITED_AT = datetime(2026, 11, 10, 9, 30, tzinfo=UTC)
_NOW = _AWAITED_AT + timedelta(seconds=90)
_PROVIDER = grievance_provider("kisan-provider", "PM-KISAN")
_ASK = an_ask()
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
        ask_id=0,
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


def test_an_init_reply_with_a_challenge_gives_the_challenge() -> None:
    reply = _reply({"challenge": {"issued": {"method": "OTP"}}})

    assert read_init_reply(reply) is ChallengeMethod.OTP


@pytest.mark.parametrize(
    "attributes",
    [{}, {"challenge": {}}, {"challenge": "OTP"}, {"enrolmentId": "x"}],
)
def test_an_init_reply_without_a_challenge_is_a_failure(attributes: dict) -> None:
    assert isinstance(read_init_reply(_reply(attributes)), InitReplyWithoutChallenge)


def test_a_failed_init_call_is_its_own_failure() -> None:
    failure = _failure(503)

    assert read_init_reply(failure) is failure


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
    ending = end_for_failed_init(
        InitReplyWithoutChallenge(), details=_DETAILS, text=_TEXT
    )

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


def _waiting(
    details: FormSubmission = _DETAILS, *, awaited_at: datetime = _AWAITED_AT
) -> FormAwaitingInit:
    return FormAwaitingInit(
        submission=details,
        provider=_PROVIDER,
        challenge_method=ChallengeMethod.OTP,
        awaited_at=awaited_at,
    )


def _sent_init(
    forms: FormRecords, *, challenge: IssuedChallenge = ChallengeMethod.OTP
) -> FlowDecision:
    init = find_init_call(_valid(_DETAILS), _FORMS)
    assert isinstance(init, InitCall)
    return decide_after_sent_init(
        forms,
        _DETAILS,
        provider=_PROVIDER,
        init=init,
        challenge=challenge,
        text=_TEXT,
        now=_NOW,
    )


def test_a_sent_init_leaves_the_details_waiting_with_provider_and_method() -> None:
    done = FormDone(submission=_DETAILS.model_copy(update={"form_id": "filed"}))

    decision = _sent_init({"filed": done})

    assert decision.forms == {"filed": done, "offer-1": _waiting(awaited_at=_NOW)}


def test_a_sent_init_asks_for_the_init_form() -> None:
    decision = _sent_init({})

    assert decision.ending.status is TurnStatus.REQUIRES_INPUT
    assert decision.ending.cause is None
    told, offered = decision.ending.answer.content
    assert told == TextBlock(text=_TEXT.init_response_needed)
    assert isinstance(offered, FormBlock)
    assert offered.form.context == PMKISAN_INIT
    assert [field.label for field in offered.form.fields] == ["Enter OTP"]


def test_an_unknown_challenge_keeps_the_path_in_words() -> None:
    decision = _sent_init({}, challenge=UnknownChallenge(sent_method="FACE"))

    _, offered = decision.ending.answer.content
    assert isinstance(offered, FormBlock)
    assert offered.form == _FORMS.find(PMKISAN_INIT)


def _ask_for(context: FormContext) -> Ask | None:
    return _ASK


def _no_ask(context: FormContext) -> Ask | None:
    return None


def test_an_init_response_finds_the_flow_waiting_for_it() -> None:
    entry = _waiting()
    action = _FORMS.action_schema(PMKISAN_SUPPORT)
    assert action is not None
    forms = {"offer-1": entry}

    flow = find_waiting_flow(forms, PMKISAN_INIT, _FORMS, ask_for=_ask_for)

    assert flow == WaitingFlow(
        entry=entry, details=_valid(_DETAILS), action=action, ask=_ASK
    )


@pytest.mark.parametrize(
    "forms",
    [
        {},
        {"offer-1": FormDone(submission=_DETAILS)},
    ],
    ids=["nothing-saved", "only-done"],
)
def test_an_init_response_with_nothing_waiting_finds_no_init_pending(
    forms: FormRecords,
) -> None:
    flow = find_waiting_flow(forms, PMKISAN_INIT, _FORMS, ask_for=_ask_for)

    assert flow == NoInitPending()


def test_an_init_response_for_another_scheme_finds_no_init_pending() -> None:
    forms = {"offer-1": _waiting()}
    pmfby_init = FormContext(scheme="PMFBY", category="Grievance", action="init")

    flow = find_waiting_flow(forms, pmfby_init, _FORMS, ask_for=_ask_for)

    assert flow == NoInitPending()


class _WithoutSupport(_WithoutInit):
    def action_schema(self, context: FormContext) -> ActionSchema | None:
        if context == PMKISAN_SUPPORT:
            return None
        return _FORMS.action_schema(context)


@pytest.mark.parametrize(
    ("entry", "forms", "ask_for"),
    [
        (
            _waiting(_submission(PMKISAN_SUPPORT, {"enrolmentId": "bad"})),
            _FORMS,
            _ask_for,
        ),
        (_waiting(), _WithoutSupport(), _ask_for),
        (_waiting(), _FORMS, _no_ask),
    ],
    ids=["details-invalid", "action-dropped", "no-ask"],
)
def test_saved_details_the_flow_cannot_use_are_unusable(
    entry: FormAwaitingInit, forms: _WithoutInit, ask_for
) -> None:
    flow = find_waiting_flow({"offer-1": entry}, PMKISAN_INIT, forms, ask_for=ask_for)

    assert flow == SavedDetailsUnusable("offer-1")


def test_nothing_pending_asks_for_the_details_again_and_keeps_the_forms() -> None:
    forms = {"offer-1": _waiting()}

    decision = decide_with_no_waiting_flow(forms, NoInitPending(), _TEXT)

    assert decision.forms == forms
    assert decision.ending.status is TurnStatus.REQUIRES_INPUT
    assert decision.ending.cause is Cause.NO_INIT_PENDING
    assert decision.ending.answer.content == (TextBlock(text=_TEXT.no_init_pending),)


def test_unusable_saved_details_end_the_flow_as_our_fault() -> None:
    forms = {"offer-1": _waiting()}

    decision = decide_with_no_waiting_flow(
        forms, SavedDetailsUnusable("offer-1"), _TEXT
    )

    assert decision.forms == {}
    assert decision.ending.status is TurnStatus.UNAVAILABLE
    assert decision.ending.cause is Cause.INTERNAL
    assert decision.ending.answer.content == (
        TextBlock(text=_TEXT.saved_details_unusable),
    )


def _flow(details: FormSubmission = _DETAILS) -> WaitingFlow:
    flow = find_waiting_flow(
        {details.form_id: _waiting(details)},
        PMKISAN_INIT,
        _FORMS,
        ask_for=_ask_for,
    )
    assert isinstance(flow, WaitingFlow)
    return flow


_INIT_RESPONSE = _submission(
    PMKISAN_INIT, {"challenge.response.value": "1234"}, form_id="otp-1"
)


def _decide(
    status_code: int | None,
    *,
    details: FormSubmission = _DETAILS,
    retry_after_seconds: int | None = None,
) -> FlowDecision:
    init_form = _FORMS.find(PMKISAN_INIT)
    assert init_form is not None
    return decide_after_failed_init_response(
        {},
        _flow(details),
        _failure(status_code, retry_after_seconds=retry_after_seconds),
        init_response=_INIT_RESPONSE,
        init_form=init_form,
        text=_TEXT,
        now=_NOW,
    )


def _init_form_handed_back(told: str) -> tuple[TextBlock, FormErrorBlock]:
    return (
        TextBlock(text=told),
        FormErrorBlock(
            form_id="otp-1",
            context=PMKISAN_INIT,
            submission_id="submission-1",
            fields=(
                FieldErrorEntry(
                    name="challenge.response.value",
                    label="Challenge response value",
                    error=_TEXT.field_recheck,
                ),
            ),
        ),
    )


def _awaiting_again() -> FormRecords:
    return {"offer-1": _waiting(awaited_at=_NOW)}


def test_a_filed_init_response_leaves_the_details_done() -> None:
    forms = with_details_form_done({}, _flow())

    assert isinstance(forms["offer-1"], FormDone)


@pytest.mark.parametrize("status_code", [400, 404, 422])
def test_a_refused_response_hands_the_init_form_back_and_waits(
    status_code: int,
) -> None:
    decision = _decide(status_code)

    assert decision.ending.status is TurnStatus.REQUIRES_INPUT
    assert decision.ending.cause is Cause.INIT_RESPONSE_REFUSED
    assert decision.ending.answer.content == _init_form_handed_back(
        _TEXT.init_response_refused
    )
    assert decision.forms == _awaiting_again()


@pytest.mark.parametrize("status_code", [408, 429, None])
def test_a_response_never_taken_in_hands_the_init_form_back_and_waits(
    status_code: int | None,
) -> None:
    decision = _decide(status_code, retry_after_seconds=30)

    assert decision.ending.status is TurnStatus.UNAVAILABLE
    assert decision.ending.cause is Cause.PROVIDER_UNAVAILABLE
    assert decision.ending.retry_after_seconds == 30
    assert decision.ending.answer.content == _init_form_handed_back(
        _TEXT.init_response_not_sent
    )
    assert decision.forms == _awaiting_again()


@pytest.mark.parametrize("status_code", [500, 503, NO_READABLE_REPLY])
def test_a_response_that_may_have_gone_through_ends_the_flow_text_only(
    status_code: int,
) -> None:
    decision = _decide(status_code, retry_after_seconds=30)

    assert decision.ending == FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.PROVIDER_OUTCOME_UNKNOWN,
        answer=decision.ending.answer,
        retry_after_seconds=None,
    )
    assert decision.ending.answer.content == (TextBlock(text=_TEXT.outcome_unknown),)
    assert decision.forms == {}


@pytest.mark.parametrize("status_code", [401, 403])
def test_our_credentials_refused_end_the_flow_as_our_fault(status_code: int) -> None:
    decision = _decide(status_code)

    assert decision.ending.status is TurnStatus.UNAVAILABLE
    assert decision.ending.cause is Cause.INTERNAL
    assert decision.ending.answer.content == (TextBlock(text=_TEXT.went_wrong),)
    assert decision.forms == {}


_STATUS_DETAILS = _submission(PMKISAN_STATUS, _KISAN_LOOKUP)


@pytest.mark.parametrize("status_code", [400, 404, 422])
def test_a_refused_status_check_ends_the_flow_with_no_such_case(
    status_code: int,
) -> None:
    decision = _decide(status_code, details=_STATUS_DETAILS)

    assert decision.ending.status is TurnStatus.UNAVAILABLE
    assert decision.ending.cause is Cause.PROVIDER_REJECTED
    assert decision.ending.answer.content == (TextBlock(text=_TEXT.case_not_found),)
    assert decision.forms == {}


@pytest.mark.parametrize("status_code", [429, 503, 401])
def test_a_status_check_reads_every_other_failure_as_support_does(
    status_code: int,
) -> None:
    support = _decide(status_code, retry_after_seconds=30)

    status = _decide(status_code, details=_STATUS_DETAILS, retry_after_seconds=30)

    assert status.ending == support.ending
