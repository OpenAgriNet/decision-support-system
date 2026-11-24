from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.forms.models import (
    ActionSchema,
    FailureExit,
    FieldErrorEntry,
    FlowEnding,
    FormAwaitingInit,
    FormContext,
    FormDefinition,
    FormErrorBlock,
    FormRecords,
    FormSubmission,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    INTERNAL_ERROR_EXIT,
    answer_for_init_form,
    answer_with_text,
    clear_awaiting_init_record,
    end_for_failed_call,
    end_for_init_without_challenge,
    exit_with_form_back,
    exit_with_text_only,
    find_awaiting_init,
    validate_submission,
    with_form_done,
    with_init_awaited,
)
from dss.core.intent.models import Ask
from dss.core.planner.models import Failure
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.core.shared.models import (
    Cause,
    ProviderAction,
    ProviderFailureKind,
    TextBlock,
    TurnStatus,
)


# The pack does not say what init returns yet; v2 packs will. Until then:
# what the init form answers is issued under `challenge.issued`.
class _Challenge(BaseModel):
    model_config = ConfigDict(frozen=True)

    issued: dict[str, Any]


class _InitReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    challenge: _Challenge


type AskFor = Callable[[FormContext], Ask | None]


@dataclass(frozen=True)
class FlowDecision:
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
class InitReplyWithoutChallenge:
    pass


type InitFailure = Failure | InitReplyWithoutChallenge | InitNotConfigured


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
) -> Failure | InitReplyWithoutChallenge | None:
    if isinstance(reply, Failure):
        return reply
    if not _has_init_challenge(reply.attributes):
        return InitReplyWithoutChallenge()
    return None


def _has_init_challenge(attributes: Mapping[str, object]) -> bool:
    try:
        _InitReply.model_validate(attributes)
    except ValidationError:
        return False
    return True


def end_for_failed_init(
    failure: InitFailure, *, details: FormSubmission, text: ClarificationText
) -> FlowEnding:
    match failure:
        case InitNotConfigured():
            return _ended_for_good(INTERNAL_ERROR_EXIT, text)
        case InitReplyWithoutChallenge():
            return end_for_init_without_challenge(details, text)
        case Failure():
            return end_for_failed_call(
                details, failure, text, action=ProviderAction.INIT
            )


def decide_after_sent_init(  # noqa: PLR0913
    forms: FormRecords,
    submission: FormSubmission,
    *,
    provider: ProviderCapability,
    init: InitCall,
    text: ClarificationText,
    now: datetime,
) -> FlowDecision:
    return FlowDecision(
        forms=with_init_awaited(forms, submission, provider=provider, now=now),
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


@dataclass(frozen=True)
class NoInitPending:
    pass


@dataclass(frozen=True)
class SavedDetailsUnusable:
    details_form_id: str


@dataclass(frozen=True)
class WaitingFlow:
    entry: FormAwaitingInit
    details: ValidSubmission
    action: ActionSchema
    ask: Ask


def find_waiting_flow(
    forms: FormRecords,
    init_context: FormContext,
    form_lookup: FormLookup,
    *,
    ask_for: AskFor,
) -> NoInitPending | SavedDetailsUnusable | WaitingFlow:
    entry = find_awaiting_init(forms, init_context)
    if entry is None:
        return NoInitPending()
    details = validate_submission(entry.submission, form_lookup)
    # The pack may have changed since the details were saved.
    action = form_lookup.action_schema(entry.submission.context)
    ask = ask_for(entry.submission.context)
    if not isinstance(details, ValidSubmission) or action is None or ask is None:
        return SavedDetailsUnusable(entry.submission.form_id)
    return WaitingFlow(entry=entry, details=details, action=action, ask=ask)


def decide_with_no_waiting_flow(
    forms: FormRecords,
    flow: NoInitPending | SavedDetailsUnusable,
    text: ClarificationText,
) -> FlowDecision:
    match flow:
        case NoInitPending():
            return FlowDecision(
                forms=forms,
                ending=FlowEnding(
                    status=TurnStatus.REQUIRES_INPUT,
                    cause=Cause.NO_INIT_PENDING,
                    answer=answer_with_text(text.no_init_pending),
                ),
            )
        case SavedDetailsUnusable():
            return FlowDecision(
                forms=clear_awaiting_init_record(forms),
                ending=FlowEnding(
                    status=TurnStatus.UNAVAILABLE,
                    cause=Cause.INTERNAL,
                    answer=answer_with_text(text.saved_details_unusable),
                ),
            )


def with_details_form_done(forms: FormRecords, flow: WaitingFlow) -> FormRecords:
    return with_form_done(forms, flow.entry.submission)


def decide_after_failed_init_response(  # noqa: PLR0913
    forms: FormRecords,
    flow: WaitingFlow,
    failure: Failure,
    *,
    init_response: FormSubmission,
    init_form: FormDefinition,
    text: ClarificationText,
    now: datetime,
) -> FlowDecision:
    action = ProviderAction(flow.action.context.action)
    exit_for_failure = _INIT_RESPONSE_EXITS[action][failure.kind]
    if not exit_for_failure.hands_form_back:
        return FlowDecision(forms=forms, ending=_ended_for_good(exit_for_failure, text))
    return FlowDecision(
        forms=with_init_awaited(
            forms, flow.entry.submission, provider=flow.entry.provider, now=now
        ),
        ending=_ended_with_init_form_back(
            exit_for_failure, failure, init_response, init_form, text
        ),
    )


_INIT_RESPONSE_EXITS: Mapping[
    ProviderAction, Mapping[ProviderFailureKind, FailureExit]
] = {
    ProviderAction.SUPPORT: {
        ProviderFailureKind.REFUSED: FailureExit(
            TurnStatus.REQUIRES_INPUT,
            Cause.INIT_RESPONSE_REFUSED,
            lambda text: text.init_response_refused,
            hands_form_back=True,
        ),
        ProviderFailureKind.NOT_SENT: exit_with_form_back(
            Cause.PROVIDER_UNAVAILABLE, lambda text: text.init_response_not_sent
        ),
        ProviderFailureKind.OUTCOME_UNKNOWN: exit_with_text_only(
            Cause.PROVIDER_OUTCOME_UNKNOWN, lambda text: text.outcome_unknown
        ),
        ProviderFailureKind.UNAUTHORISED: INTERNAL_ERROR_EXIT,
    },
    ProviderAction.STATUS: {
        ProviderFailureKind.REFUSED: exit_with_text_only(
            Cause.PROVIDER_REJECTED, lambda text: text.case_not_found
        ),
        ProviderFailureKind.NOT_SENT: exit_with_form_back(
            Cause.PROVIDER_UNAVAILABLE, lambda text: text.init_response_not_sent
        ),
        ProviderFailureKind.OUTCOME_UNKNOWN: exit_with_text_only(
            Cause.PROVIDER_OUTCOME_UNKNOWN, lambda text: text.outcome_unknown
        ),
        ProviderFailureKind.UNAUTHORISED: INTERNAL_ERROR_EXIT,
    },
}


def _ended_for_good(
    exit_for_failure: FailureExit, text: ClarificationText
) -> FlowEnding:
    return FlowEnding(
        status=exit_for_failure.status,
        cause=exit_for_failure.cause,
        answer=answer_with_text(exit_for_failure.text(text)),
    )


# Only a form handed back says when to send it again.
def _ended_with_init_form_back(
    exit_for_failure: FailureExit,
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
