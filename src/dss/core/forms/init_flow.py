from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus

from dss.core.channel.models import ClarificationText
from dss.core.forms.models import (
    ActionSchema,
    FailedCallOutcome,
    FlowEnding,
    FormContext,
    FormDefinition,
    FormRecords,
    FormSubmission,
    ProviderAction,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    answer_for_init_form,
    answer_with_text,
    end_for_call_cause,
    is_auth_refused,
    with_init_awaited,
)
from dss.core.planner.models import NO_READABLE_REPLY, Failure
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.core.shared.models import Cause, TurnStatus

# The pack does not say what init returns yet; v2 packs will. Until then:
# what the init form answers is issued under `challenge.issued`.
_INIT_REPLY_PATH = ("challenge", "issued")


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
) -> FlowSettled:
    return FlowSettled(
        forms=with_init_awaited(
            forms,
            submission.form_id,
            details.definition,
            submission,
            provider=provider,
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
