from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial

from dss.adapters.observability.tracing import (
    TurnRecorder,
    set_current_span_attributes,
    set_current_span_content,
)
from dss.core.channel.localize import LocalizeText, localize_answer
from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.forms.challenge import IssuedChallenge
from dss.core.forms.init_flow import (
    FlowDecision,
    InitCall,
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
    read_init_reply,
    with_details_form_done,
)
from dss.core.forms.models import (
    ActionSchema,
    FlowEnding,
    FormContext,
    FormErrors,
    FormSubmission,
    UnknownForm,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    clear_awaiting_init_record,
    discovery_for_scheme,
    end_for_already_filed,
    end_for_failed_call,
    end_for_form_errors,
    end_for_invalid_form,
    end_for_no_provider,
    end_for_unknown_form,
    free_text_of,
    is_auth_refused,
    is_done,
    mask_pii_fields,
    validate_submission,
    with_form_done,
    withhold_values,
)
from dss.core.intent.models import Ask, Scheme
from dss.core.intent.service import scheme_with_code
from dss.core.moderation.models import ModerationContext, ModerationDecision, Outcome
from dss.core.moderation.service import moderate
from dss.core.planner.evidence import assemble_evidence
from dss.core.planner.models import Evidence, Failure
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.redaction.turn import surrounding_texts_of, with_surrounding_texts
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import (
    ProviderAction,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnOutcome,
    UserTurn,
)
from dss.core.stream_response.service import ComposeStream
from dss.observability.stages import Stage
from dss.observability.trace_log import trace_component
from dss.orchestration.discovery import DiscoverProviders
from dss.orchestration.form_offer import ask_for_form
from dss.orchestration.orchestrator import (
    build_turn_finished,
    compose_answer,
    note_stage,
    outcome_for,
    refusal_for,
    reveal_to_farmer,
    spoken_text,
)
from dss.orchestration.provider_action import call_provider_action, warn_auth_refused
from dss.orchestration.redaction import RedactTexts
from dss.orchestration.session import Session
from dss.orchestration.stage_content import (
    asks_text,
    discovery_text,
    provider_response_text,
)
from dss.orchestration.turn import moderation_attributes
from dss.ports.invocation import ProviderActionInvocation
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider
from dss.ports.scheme_catalog import SchemeCatalog
from dss.ports.sinks import TelemetrySink, TurnSink

# Several prose fields are judged as one text, like one question.
_FREE_TEXT_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class _FormToSend:
    details: ValidSubmission
    ask: Ask
    action: ActionSchema


@dataclass(frozen=True)
class _RedactedTexts:
    turn: UserTurn[FormSubmission]
    free_text: str
    visibility: Visibility


@dataclass(frozen=True)
class _Redacted:
    turn: UserTurn[FormSubmission]  # for the record: values withheld
    # The submitted form, PII fields masked; one that failed its checks as is.
    form: ValidSubmission | FormErrors | UnknownForm
    free_text: str
    # The real values behind the tags, for the provider call only.
    visibility: Visibility


# Which turn this is; the session is apart, since it changes during the turn.
@dataclass(frozen=True)
class _FormTurn:
    turn: UserTurn[FormSubmission]
    ctx: TurnContext
    recorder: TurnRecorder


# One fixed answer, or the composer's stream.
type _Reply = TurnFinished | AsyncGenerator[TurnEvent]


@dataclass(frozen=True)
class FormPathComponents:
    redact: RedactTexts
    discover: DiscoverProviders
    compose: ComposeStream
    localize: LocalizeText
    invocation_for: Callable[[UserTurn], ProviderActionInvocation]


class FormPath:
    def __init__(  # noqa: PLR0913
        self,
        *,
        moderation_llm: LLMProvider,
        prompts: PromptProvider,
        policies: Sequence[Policy],
        forms: FormLookup,
        scheme_catalog: SchemeCatalog,
        components: FormPathComponents,
        turns: TurnSink,
        telemetry: TelemetrySink,
        clarification_text: ClarificationText,
    ) -> None:
        self._moderation_llm = moderation_llm
        self._prompts = prompts
        self._policies = policies
        self._forms = forms
        self._scheme_catalog = scheme_catalog
        self._components = components
        self._turns = turns
        self._telemetry = telemetry
        self._clarification_text = clarification_text

    async def run(
        self,
        turn: UserTurn[FormSubmission],
        *,
        ctx: TurnContext,
        recorder: TurnRecorder,
        session: Session,
    ) -> AsyncGenerator[TurnEvent]:
        form_turn = _FormTurn(turn=turn, ctx=ctx, recorder=recorder)
        submission = turn.input
        handle = (
            self._handle_init_response
            if submission.context.action == ProviderAction.INIT
            else self._handle_details_form
        )
        reply = await handle(form_turn, session)
        async with aclosing(_events_of(reply)) as events:
            async for event in events:
                yield event

    async def _handle_details_form(  # noqa: PLR0911
        self, form_turn: _FormTurn, session: Session
    ) -> _Reply:
        # Validated before masking: a tagged value would fail its own pattern.
        redaction: _Redacted = await self._redact_form(
            form_turn, validate_submission(form_turn.turn.input, self._forms)
        )
        # Opened before any check: every exit writes the record. It holds the
        # history and phone tagged and the form's values withheld.
        self._turns.opened(form_turn.ctx, redaction.turn)

        submission = redaction.turn.input  # values withheld: ids only

        details = redaction.form  # PII fields masked
        action = self._forms.action_schema(submission.context)
        ask = self._ask_for(submission.context)

        if isinstance(details, UnknownForm) or action is None or ask is None:
            return await self._finish_turn(
                end_for_unknown_form(self._clarification_text), form_turn
            )

        if is_done(session.forms, submission.form_id):
            return await self._finish_turn(
                end_for_already_filed(self._clarification_text), form_turn
            )

        if isinstance(details, FormErrors):
            return await self._finish_turn(
                end_for_form_errors(details, self._clarification_text), form_turn
            )
        # Before init: an init response could never find this form again.
        if action.needs_init() and not session.is_kept:
            return await self._finish_turn(
                end_for_session_not_kept(self._clarification_text), form_turn
            )

        form = _FormToSend(details=details, ask=ask, action=action)
        decision = await self._moderate(redaction, form_turn)
        if decision.outcome is not Outcome.PROCEED:
            return await self._refuse(decision, form_turn)
        provider = await self._find_form_provider(form.ask, form_turn)
        if provider is None:
            return await self._finish_turn(
                end_for_no_provider(form.ask, self._clarification_text), form_turn
            )
        # One form waits for init at a time: this form's call replaces any
        # other, even another scheme's, and even when this form needs no init.
        session.update_forms(clear_awaiting_init_record(session.forms))
        # Checked above for the session; here it picks the call: init first,
        # and the form itself is sent once the init response comes back.
        if form.action.needs_init():
            return await self._send_init_and_await_response(
                form_turn,
                form.details,
                provider=provider,
                redaction=redaction,
                session=session,
            )
        return await self._send_form_and_stream_answer(
            form_turn, form, provider, redaction, session
        )

    async def _send_form_and_stream_answer(
        self,
        form_turn: _FormTurn,
        form: _FormToSend,
        provider: ProviderCapability,
        redaction: _Redacted,
        session: Session,
    ) -> _Reply:
        response = await self._call_provider_action(
            form_turn,
            form.details,
            form.action,
            provider=provider,
            visibility=redaction.visibility,
        )
        if isinstance(response, Failure):
            _warn_if_auth_refused(response, form_turn.ctx)
            return await self._finish_turn(
                end_for_failed_call(
                    redaction.turn.input,
                    response,
                    self._clarification_text,
                    action=form.action,
                ),
                form_turn,
            )
        # Before the answer streams: a closed screen must still leave it done.
        session.update_forms(with_form_done(session.forms, redaction.turn.input))
        return self._stream_provider_answer(response, form.ask, redaction, form_turn)

    async def _handle_init_response(
        self, form_turn: _FormTurn, session: Session
    ) -> _Reply:
        # Redacted like any form (the history, and the init response masked),
        # but never moderated: it has no free text. Discovery and the done
        # check ran when the details form was sent.
        flow = find_waiting_flow(
            session.forms,
            form_turn.turn.input.context,
            self._forms,
            ask_for=self._ask_for,
        )
        redaction = await self._redact_form(
            form_turn, validate_submission(form_turn.turn.input, self._forms)
        )
        # Opened before any check: every exit writes the record. It holds the
        # history and phone tagged and the form's values withheld.
        self._turns.opened(form_turn.ctx, redaction.turn)
        match flow:
            case WaitingFlow():
                return await self._send_init_response(
                    form_turn, flow, redaction, session
                )
            case NoInitPending() | SavedDetailsUnusable():
                return await self._update_forms_and_end(
                    decide_with_no_waiting_flow(
                        session.forms, flow, self._clarification_text
                    ),
                    session,
                    form_turn,
                )

    async def _send_init_response(
        self,
        form_turn: _FormTurn,
        flow: WaitingFlow,
        redaction: _Redacted,
        session: Session,
    ) -> _Reply:
        init_response = redaction.form
        if not isinstance(init_response, ValidSubmission):
            return await self._finish_turn(
                end_for_invalid_form(init_response, self._clarification_text),
                form_turn,
            )
        # The saved details are a second form: masked too, and their tags held
        # with the turn's, so the answer can reveal what the provider echoes.
        details = mask_pii_fields(flow.details)
        held = redaction.visibility
        redaction = replace(
            redaction,
            visibility=Visibility(
                values={**held.values, **details.visibility.values},
                never_concealed=held.never_concealed
                | details.visibility.never_concealed,
            ),
        )
        # Removed before the call: a crash after it must not leave a response
        # that may have reached the provider open to be used again. A failure
        # the provider never took puts it back (`decide_after_failed_init_response`).
        session.update_forms(clear_awaiting_init_record(session.forms))
        response = await self._call_provider_action(
            form_turn,
            details.submission,
            flow.action,
            provider=flow.entry.provider,
            visibility=redaction.visibility,
            init_values=init_response.values,
        )
        if isinstance(response, Failure):
            _warn_if_auth_refused(response, form_turn.ctx)
            return await self._update_forms_and_end(
                decide_after_failed_init_response(
                    session.forms,
                    flow,
                    response,
                    init_response=redaction.turn.input,
                    init_form=init_response.definition,
                    text=self._clarification_text,
                    now=datetime.now(UTC),
                ),
                session,
                form_turn,
            )
        session.update_forms(with_details_form_done(session.forms, flow))
        return self._stream_provider_answer(response, flow.ask, redaction, form_turn)

    def _stream_provider_answer(
        self,
        response: DiscoveredAnswer,
        ask: Ask,
        redaction: _Redacted,
        form_turn: _FormTurn,
    ) -> AsyncGenerator[TurnEvent]:
        evidence = assemble_evidence([(ask.id, response)], asks=(ask,))
        return self._stream_answer(evidence, redaction, form_turn)

    async def _stream_answer(
        self, evidence: Evidence, redaction: _Redacted, form_turn: _FormTurn
    ) -> AsyncGenerator[TurnEvent]:
        # The record only ever sees the tagged turn.
        recorded = replace(form_turn, turn=redaction.turn)
        # `aclosing` each layer: closing an async generator does not reach the
        # one it reads from, and a closed screen must close the model stream.
        async with (
            aclosing(
                compose_answer(
                    evidence,
                    turn=redaction.turn,
                    compose=self._components.compose,
                    localize=self._components.localize,
                    ctx=form_turn.ctx,
                    recorder=form_turn.recorder,
                )
            ) as core_events,
            aclosing(
                reveal_to_farmer(
                    core_events,
                    visibility=redaction.visibility,
                    recorder=form_turn.recorder,
                    on_finished=partial(self._record, form_turn=recorded),
                )
            ) as farmer_events,
        ):
            async for event in farmer_events:
                yield event

    async def _send_init_and_await_response(
        self,
        form_turn: _FormTurn,
        details: ValidSubmission,
        *,
        provider: ProviderCapability,
        redaction: _Redacted,
        session: Session,
    ) -> TurnFinished:
        submission = redaction.turn.input
        init = find_init_call(details, self._forms)
        if not isinstance(init, InitCall):
            return await self._finish_turn(
                end_for_failed_init(
                    init, details=submission, text=self._clarification_text
                ),
                form_turn,
            )
        reply: DiscoveredAnswer | Failure = await self._call_provider_action(
            form_turn,
            details,
            init.action,
            provider=provider,
            visibility=redaction.visibility,
        )
        init_reply = read_init_reply(reply)
        if not isinstance(init_reply, IssuedChallenge):
            _warn_if_auth_refused(init_reply, form_turn.ctx)
            return await self._finish_turn(
                end_for_failed_init(
                    init_reply, details=submission, text=self._clarification_text
                ),
                form_turn,
            )
        return await self._update_forms_and_end(
            # The details are kept as typed, not masked: the call after the init
            # response needs the real values, and the tags last one turn. A done
            # form keeps none.
            decide_after_sent_init(
                session.forms,
                form_turn.turn.input,
                provider=provider,
                init=init,
                challenge=init_reply,
                text=self._clarification_text,
                now=datetime.now(UTC),
            ),
            session,
            form_turn,
        )

    async def _update_forms_and_end(
        self, decision: FlowDecision, session: Session, form_turn: _FormTurn
    ) -> TurnFinished:
        session.update_forms(decision.forms)
        return await self._finish_turn(decision.ending, form_turn)

    async def _finish_turn(
        self, ending: FlowEnding, form_turn: _FormTurn
    ) -> TurnFinished:
        outcome = outcome_for(
            ending.status,
            ending.cause,
            retry_after_seconds=ending.retry_after_seconds,
        )
        return await self._finish_with_outcome(outcome, ending.answer, form_turn)

    async def _call_provider_action(  # noqa: PLR0913
        self,
        form_turn: _FormTurn,
        submission: ValidSubmission,
        action: ActionSchema,
        *,
        provider: ProviderCapability,
        visibility: Visibility,
        init_values: Mapping[str, str] | None = None,
    ) -> DiscoveredAnswer | Failure:
        turn = form_turn.turn
        with trace_component(Stage.PROVIDER_ACTION, form_turn.ctx.trace_id):
            response = await call_provider_action(
                provider,
                action,
                submission.values,
                invocation=self._components.invocation_for(turn),
                transaction_id=turn.transaction_id,
                visibility=visibility,
                init_values=init_values,
            )
            set_current_span_attributes(
                action=action.context.action,
                provider_id=provider.provider_id,
                is_failure=isinstance(response, Failure),
            )
            set_current_span_content(
                input=f"{action.context.action} to {provider.provider_name}",
                output=provider_response_text(response),
            )
        return response

    def _ask_for(self, context: FormContext) -> Ask | None:
        scheme = scheme_with_code(self._scheme_catalog.schemes(), context.scheme)
        if scheme is None:
            scheme = Scheme(code=context.scheme, name=context.scheme)
        return ask_for_form(context, scheme=scheme)

    async def _find_form_provider(
        self, ask: Ask, form_turn: _FormTurn
    ) -> ProviderCapability | None:
        discovery = await self._find_providers(
            ask, form_turn, scheme_code=form_turn.turn.input.context.scheme
        )
        discovered = discovery.find(ask.id)
        # form's action needs a provider to call and not a direct answer
        providers = discovered.capabilities if discovered else ()
        return providers[0] if providers else None

    async def _find_providers(
        self,
        ask: Ask,
        form_turn: _FormTurn,
        *,
        scheme_code: str,
    ) -> DiscoveryResult:
        with trace_component(Stage.DISCOVERY, form_turn.ctx.trace_id):
            found = await self._components.discover(
                (ask,), form_turn.turn, now=datetime.now(UTC)
            )
            providers = discovery_for_scheme(found, scheme_code)
            set_current_span_attributes(
                providers_found=_count(found),
                providers_for_scheme=_count(providers),
            )
            set_current_span_content(
                input=asks_text((ask,)), output=discovery_text(providers)
            )
        return providers

    async def _redact_form(
        self,
        form_turn: _FormTurn,
        validated_form: ValidSubmission | FormErrors | UnknownForm,
    ) -> _Redacted:
        masked_form = None
        form = validated_form

        if isinstance(validated_form, ValidSubmission):
            masked_form = mask_pii_fields(validated_form)
            form = masked_form.submission

        texts = await self._redact_texts(form_turn, _free_text_of(form))
        held = masked_form.visibility if masked_form is not None else NOTHING_HELD
        return _Redacted(
            turn=texts.turn,
            form=form,
            free_text=texts.free_text,
            visibility=Visibility(
                values={**texts.visibility.values, **held.values},
                never_concealed=texts.visibility.never_concealed | held.never_concealed,
            ),
        )

    async def _redact_texts(
        self, form_turn: _FormTurn, free_text: Sequence[str]
    ) -> _RedactedTexts:
        turn, ctx = form_turn.turn, form_turn.ctx
        surroundings = surrounding_texts_of(turn)
        with trace_component(Stage.REDACTION, ctx.trace_id):
            redaction = await self._components.redact(
                [*surroundings, *free_text], transaction_id=turn.transaction_id
            )
        form_turn.recorder.redacted(found=redaction.found, failed=redaction.failed)
        tagged_surroundings = redaction.texts[: len(surroundings)]
        tagged_free_text = redaction.texts[len(surroundings) :]
        return _RedactedTexts(
            turn=_with_values_withheld(
                with_surrounding_texts(turn, tagged_surroundings)
            ),
            free_text=_FREE_TEXT_SEPARATOR.join(tagged_free_text),
            visibility=redaction.visibility,
        )

    async def _moderate(
        self, redaction: _Redacted, form_turn: _FormTurn
    ) -> ModerationDecision:
        decision = ModerationDecision(outcome=Outcome.PROCEED)
        if redaction.free_text:
            with trace_component(Stage.MODERATION, form_turn.ctx.trace_id):
                decision = await moderate(
                    ModerationContext.for_text(redaction.turn, redaction.free_text),
                    self._policies,
                    self._moderation_llm,
                    self._prompts,
                )
                set_current_span_attributes(**moderation_attributes(decision))
                set_current_span_content(input=redaction.free_text)
        note_stage(self._telemetry, "moderation", form_turn.ctx, decision.outcome.value)
        return decision

    async def _refuse(
        self, decision: ModerationDecision, form_turn: _FormTurn
    ) -> TurnFinished:
        # Not _finish_turn: TextPath shares refusal_for, which builds the outcome.
        outcome, refusal = refusal_for(decision)
        return await self._finish_with_outcome(outcome, refusal, form_turn)

    async def _finish_with_outcome(
        self, outcome: TurnOutcome, answer: ComposedAnswer, form_turn: _FormTurn
    ) -> TurnFinished:
        answer = await localize_answer(
            answer, self._components.localize, turn=form_turn.turn
        )
        finished = build_turn_finished(outcome, answer)
        self._record(finished, form_turn=form_turn)
        return finished

    def _record(self, finished: TurnFinished, *, form_turn: _FormTurn) -> None:
        self._turns.closed(form_turn.ctx, finished)
        form_turn.recorder.status(finished.outcome.status.value)
        form_turn.recorder.content(
            query=_reference_line(form_turn.turn.input),
            answer=spoken_text(finished.content),
        )


async def _events_of(reply: _Reply) -> AsyncGenerator[TurnEvent]:
    if isinstance(reply, TurnFinished):
        yield reply
        return
    async with aclosing(reply) as events:
        async for event in events:
            yield event


def _free_text_of(
    form: ValidSubmission | FormErrors | UnknownForm,
) -> tuple[str, ...]:
    return free_text_of(form) if isinstance(form, ValidSubmission) else ()


def _warn_if_auth_refused(
    failure: Failure | InitReplyWithoutChallenge, ctx: TurnContext
) -> None:
    if isinstance(failure, Failure) and is_auth_refused(failure):
        warn_auth_refused(failure, request_id=ctx.trace_id)


def _count(discovery: DiscoveryResult) -> int:
    return sum(len(discovered.capabilities) for discovered in discovery.asks)


def _with_values_withheld(
    turn: UserTurn[FormSubmission],
) -> UserTurn[FormSubmission]:
    return turn.model_copy(update={"input": withhold_values(turn.input)})


def _reference_line(submission: FormSubmission) -> str:
    names = ", ".join(value.name for value in submission.values)
    return f"form {submission.form_id} ({submission.context}): {names}"
