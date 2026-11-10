from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus

from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.forms.models import (
    ActionSchema,
    FailedCallOutcome,
    FieldErrorEntry,
    FlowEnding,
    FormContext,
    FormDefinition,
    FormEntry,
    FormErrorBlock,
    FormRecords,
    FormSubmission,
    ProviderAction,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    TextChoice,
    answer_for_init_form,
    answer_with_text,
    awaiting_init_for,
    clear_awaiting_init_record,
    end_for_call_cause,
    is_auth_refused,
    validate_submission,
    with_form_done,
    with_init_awaited,
)
from dss.core.intent.models import Intent
from dss.core.planner.models import NO_READABLE_REPLY, Failure
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.core.shared.models import Cause, TextBlock, TurnStatus

# The pack does not say what init returns yet; v2 packs will. Until then:
# what the init form answers is issued under `challenge.issued`.
_INIT_REPLY_PATH = ("challenge", "issued")

type IntentFor = Callable[[FormContext], Intent | None]


@dataclass(frozen=True)
class FlowSettled:
    forms: FormRecords
    ending: FlowEnding


@dataclass(frozen=True)
class InitCall:
    action: ActionSchema
    init_form: FormDefinition


@dataclass(frozen=True)
class InitNotConfigured:
    pass


@dataclass(frozen=True)
class NoInitChallenge:
    pass


type InitFailure = Failure | NoInitChallenge | InitNotConfigured


def find_init_call(
    details: ValidSubmission, forms: FormLookup
) -> InitCall | InitNotConfigured:
    init_context = _init_context_of(details.definition.context)
    action = forms.action_schema(init_context)
    init_form = forms.find(init_context)
    # Nothing checks at boot that a scheme needing init gives one.
    if (
        action is None
        or init_form is None
        or not _has_values_for(action, details.values)
    ):
        return InitNotConfigured()
    return InitCall(action=action, init_form=init_form)


def _init_context_of(context: FormContext) -> FormContext:
    return context.model_copy(update={"action": ProviderAction.INIT.value})


def _has_values_for(schema: ActionSchema, values: Mapping[str, str]) -> bool:
    return all(field.path in values for field in schema.request_fields())


def find_init_failure(
    reply: DiscoveredAnswer | Failure,
) -> Failure | NoInitChallenge | None:
    if isinstance(reply, Failure):
        return reply
    if not _has_init_challenge(reply.attributes):
        return NoInitChallenge()
    return None


def _has_init_challenge(attributes: Mapping[str, object]) -> bool:
    node: object = attributes
    for segment in _INIT_REPLY_PATH:
        if not isinstance(node, Mapping):
            return False
        node = node.get(segment)
    return isinstance(node, Mapping)


def end_for_failed_init(
    failure: InitFailure, *, details: FormSubmission, text: ClarificationText
) -> FlowEnding:
    match failure:
        case InitNotConfigured():
            return _ended_by_our_fault(text)
        case NoInitChallenge():
            return end_for_call_cause(
                details, Cause.PROVIDER_REJECTED, text, action=ProviderAction.INIT
            )
        case Failure() if is_auth_refused(failure):
            return _ended_by_our_fault(text)
        case Failure():
            return end_for_call_cause(
                details,
                _cause_for_failed_init(failure),
                text,
                action=ProviderAction.INIT,
                retry_after_seconds=failure.retry_after_seconds,
            )


def settle_sent_init(  # noqa: PLR0913
    forms: FormRecords,
    details: ValidSubmission,
    submission: FormSubmission,
    *,
    provider: ProviderCapability,
    init: InitCall,
    text: ClarificationText,
    now: datetime,
) -> FlowSettled:
    return FlowSettled(
        forms=with_init_awaited(
            forms,
            submission.form_id,
            details.definition,
            submission,
            provider=provider,
            now=now,
        ),
        ending=FlowEnding(
            status=TurnStatus.REQUIRES_INPUT,
            cause=None,
            answer=answer_for_init_form(init.init_form, text.init_response_needed),
        ),
    )


# Internal, not provider_unavailable: the provider is fine; the DSS cannot
# hold the flow until the init response comes back.
def end_for_session_not_kept(text: ClarificationText) -> FlowEnding:
    return FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.INTERNAL,
        answer=answer_with_text(text.form_cannot_complete),
    )


def _ended_by_our_fault(text: ClarificationText) -> FlowEnding:
    return FlowEnding(
        status=TurnStatus.UNAVAILABLE,
        cause=Cause.INTERNAL,
        answer=answer_with_text(text.went_wrong),
    )


# Init is never retried, so `retryable` does not matter; the status says
# whether the provider refused the details.
def _cause_for_failed_init(failure: Failure) -> Cause:
    if outcome_of_failed_call(failure) is FailedCallOutcome.REFUSED:
        return Cause.PROVIDER_REJECTED
    return Cause.PROVIDER_UNAVAILABLE


# The provider said it did not take the request in.
_NOT_TAKEN_IN_STATUS_CODES = frozenset(
    {HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.TOO_MANY_REQUESTS}
)


# Read from the status alone, not `retryable`: the client calls only
# 400/401/403 a defect, so a 404 or 422 refusal would read as retryable.
def outcome_of_failed_call(failure: Failure) -> FailedCallOutcome:
    status_code = failure.status_code
    if status_code is None or status_code in _NOT_TAKEN_IN_STATUS_CODES:
        return FailedCallOutcome.NOT_SENT
    if status_code == NO_READABLE_REPLY:
        return FailedCallOutcome.OUTCOME_UNKNOWN
    if _is_client_error(status_code):
        return FailedCallOutcome.REFUSED
    return FailedCallOutcome.OUTCOME_UNKNOWN


def _is_client_error(status_code: int) -> bool:
    return HTTPStatus.BAD_REQUEST <= status_code < HTTPStatus.INTERNAL_SERVER_ERROR


@dataclass(frozen=True)
class NoInitPending:
    pass


@dataclass(frozen=True)
class SavedDetailsUnusable:
    details_form_id: str


@dataclass(frozen=True)
class WaitingFlow:
    details_form_id: str
    entry: FormEntry
    details: ValidSubmission
    action: ActionSchema
    intent: Intent
    provider: ProviderCapability


def find_waiting_flow(
    forms: FormRecords,
    init_context: FormContext,
    form_lookup: FormLookup,
    *,
    intent_for: IntentFor,
) -> NoInitPending | SavedDetailsUnusable | WaitingFlow:
    details_form_id = awaiting_init_for(forms, init_context)
    if details_form_id is None:
        return NoInitPending()
    entry = forms[details_form_id]
    details = validate_submission(entry.submission, form_lookup)
    # The pack may have changed since the details were saved.
    action = form_lookup.action_schema(entry.definition.context)
    intent = intent_for(entry.definition.context)
    if (
        not isinstance(details, ValidSubmission)
        or action is None
        or intent is None
        or entry.provider is None
    ):
        return SavedDetailsUnusable(details_form_id)
    return WaitingFlow(
        details_form_id=details_form_id,
        entry=entry,
        details=details,
        action=action,
        intent=intent,
        provider=entry.provider,
    )


def settle_no_waiting_flow(
    forms: FormRecords,
    flow: NoInitPending | SavedDetailsUnusable,
    text: ClarificationText,
) -> FlowSettled:
    match flow:
        case NoInitPending():
            return FlowSettled(
                forms=forms,
                ending=FlowEnding(
                    status=TurnStatus.REQUIRES_INPUT,
                    cause=Cause.NO_INIT_PENDING,
                    answer=answer_with_text(text.no_init_pending),
                ),
            )
        case SavedDetailsUnusable():
            return FlowSettled(
                forms=clear_awaiting_init_record(forms),
                ending=FlowEnding(
                    status=TurnStatus.UNAVAILABLE,
                    cause=Cause.INTERNAL,
                    answer=answer_with_text(text.saved_details_unusable),
                ),
            )


def with_init_response_filed(forms: FormRecords, flow: WaitingFlow) -> FormRecords:
    return with_form_done(
        forms, flow.details_form_id, flow.entry.definition, flow.entry.submission
    )


def settle_failed_init_response(  # noqa: PLR0913
    forms: FormRecords,
    flow: WaitingFlow,
    failure: Failure,
    *,
    init_response: FormSubmission,
    init_form: FormDefinition,
    text: ClarificationText,
    now: datetime,
) -> FlowSettled:
    exit_for_failure = _init_response_exit_for(failure)
    if not exit_for_failure.should_await_init:
        return FlowSettled(forms=forms, ending=_ended_for_good(exit_for_failure, text))
    return FlowSettled(
        forms=with_init_awaited(
            forms,
            flow.details_form_id,
            flow.entry.definition,
            flow.entry.submission,
            provider=flow.provider,
            now=now,
        ),
        ending=_ended_with_init_form_back(
            exit_for_failure, failure, init_response, init_form, text
        ),
    )


@dataclass(frozen=True)
class _InitResponseExit:
    status: TurnStatus
    cause: Cause
    text: TextChoice
    # The provider did not use the response: the farmer may answer again.
    should_await_init: bool


# Turn 3's exits when the call with the init response failed, by what the
# status code says happened to the response.
_INIT_RESPONSE_EXITS: Mapping[FailedCallOutcome, _InitResponseExit] = {
    FailedCallOutcome.REFUSED: _InitResponseExit(
        TurnStatus.REQUIRES_INPUT,
        Cause.INIT_RESPONSE_REFUSED,
        lambda text: text.init_response_refused,
        should_await_init=True,
    ),
    FailedCallOutcome.NOT_SENT: _InitResponseExit(
        TurnStatus.UNAVAILABLE,
        Cause.PROVIDER_UNAVAILABLE,
        lambda text: text.init_response_not_sent,
        should_await_init=True,
    ),
    # It may have reached the provider: no form back, a resend might file twice.
    FailedCallOutcome.OUTCOME_UNKNOWN: _InitResponseExit(
        TurnStatus.UNAVAILABLE,
        Cause.PROVIDER_OUTCOME_UNKNOWN,
        lambda text: text.outcome_unknown,
        should_await_init=False,
    ),
}
# 401/403 refuse our own credentials: asking for the response again cannot help.
_AUTH_REFUSED_EXIT = _InitResponseExit(
    TurnStatus.UNAVAILABLE,
    Cause.INTERNAL,
    lambda text: text.went_wrong,
    should_await_init=False,
)


def _init_response_exit_for(failure: Failure) -> _InitResponseExit:
    if is_auth_refused(failure):
        return _AUTH_REFUSED_EXIT
    return _INIT_RESPONSE_EXITS[outcome_of_failed_call(failure)]


def _ended_for_good(
    exit_for_failure: _InitResponseExit, text: ClarificationText
) -> FlowEnding:
    return FlowEnding(
        status=exit_for_failure.status,
        cause=exit_for_failure.cause,
        answer=answer_with_text(exit_for_failure.text(text)),
    )


# Only a form handed back says when to send it again.
def _ended_with_init_form_back(
    exit_for_failure: _InitResponseExit,
    failure: Failure,
    init_response: FormSubmission,
    init_form: FormDefinition,
    text: ClarificationText,
) -> FlowEnding:
    return FlowEnding(
        status=exit_for_failure.status,
        cause=exit_for_failure.cause,
        answer=_init_form_handed_back(
            init_response,
            init_form,
            told=exit_for_failure.text(text),
            field_error=text.field_recheck,
        ),
        retry_after_seconds=failure.retry_after_seconds,
    )


# The init form is still the farmer's: the error sits on what they answered.
def _init_form_handed_back(
    init_response: FormSubmission,
    init_form: FormDefinition,
    *,
    told: str,
    field_error: str,
) -> ComposedAnswer:
    return ComposedAnswer(
        content=(
            TextBlock(text=told),
            FormErrorBlock(
                form_id=init_response.form_id,
                context=init_response.context,
                submission_id=init_response.submission_id,
                fields=tuple(
                    FieldErrorEntry(
                        name=field.name, label=field.label, error=field_error
                    )
                    for field in init_form.fields
                ),
            ),
        )
    )
