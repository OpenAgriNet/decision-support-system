from __future__ import annotations

from collections.abc import AsyncGenerator, Callable, Sequence
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
from dss.core.forms.init_flow import (
    FlowSettled,
    InitCall,
    NoInitChallenge,
    end_for_failed_init,
    end_for_session_not_kept,
    find_init_call,
    find_init_failure,
    settle_sent_init,
)
from dss.core.forms.models import (
    ActionSchema,
    FlowEnding,
    FormContext,
    FormErrors,
    FormSubmission,
    ProviderAction,
    UnknownForm,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    clear_awaiting_init_record,
    discovery_for_scheme,
    end_for_already_filed,
    end_for_failed_call,
    end_for_invalid_form,
    end_for_no_init_pending,
    end_for_no_provider,
    end_for_unknown_form,
    free_text_of,
    is_auth_refused,
    is_done,
    validate_submission,
    with_call_filed,
    withhold_values,
)
from dss.core.intent.models import Intent, Scheme
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
from dss.core.redaction.visibility import Visibility
from dss.core.shared.models import (
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
from dss.orchestration.provider_action import (
    FORM_ASK_INDEX,
    call_provider_action,
    warn_auth_refused,
)
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
class _Redacted:
    turn: UserTurn[FormSubmission]
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
            else self._handle_action_form
        )
        reply = await handle(form_turn, session)
        async with aclosing(_events_of(reply)) as events:
            async for event in events:
                yield event

    async def _handle_action_form(  # noqa: PLR0911 - one exit per way a form is not sent
        self, form_turn: _FormTurn, session: Session
    ) -> _Reply:
        submission = form_turn.turn.input
        # Raw values are checked first: a tagged phone fails its own pattern.
        checked = validate_submission(submission, self._forms)
        redacted = await self._redact_for_the_record(form_turn, _free_text_of(checked))

        if is_done(session.forms, submission.form_id):
            return await self._end(
                end_for_already_filed(self._clarification_text), form_turn
            )
        if not isinstance(checked, ValidSubmission):
            return await self._end(
                end_for_invalid_form(checked, self._clarification_text), form_turn
            )

        # Before moderation: a form that cannot be sent is not worth a model call.
        intent = self._intent_for(submission.context)
        if intent is None:  # a known form, but not an action the DSS can file
            return await self._end(
                end_for_unknown_form(self._clarification_text), form_turn
            )

        action = self._forms.action_schema(submission.context)
        # shortcut: the registry builds each form from its schema, so this should
        # not happen; link the two so it cannot, then drop this branch.
        if action is None:
            return await self._end(
                end_for_unknown_form(self._clarification_text), form_turn
            )
        # Before init: an init response could never find this form again.
        if action.needs_init() and not session.is_kept:
            return await self._end(
                end_for_session_not_kept(self._clarification_text), form_turn
            )

        decision = await self._moderate(redacted, form_turn)
        if decision.outcome is not Outcome.PROCEED:
            return await self._refuse(decision, form_turn)

        provider = await self._find_form_provider(intent, form_turn)
        if provider is None:
            return await self._end(
                end_for_no_provider(intent, self._clarification_text), form_turn
            )

        # One form waits for init at a time: this form's call replaces any other.
        session.update_forms(clear_awaiting_init_record(session.forms))
        if action.needs_init():
            return await self._send_init(
                form_turn,
                checked,
                provider=provider,
                visibility=redacted.visibility,
                session=session,
            )
        return await self._call_and_compose(
            form_turn,
            checked,
            action,
            intent=intent,
            provider=provider,
            redacted=redacted,
            session=session,
        )

    async def _call_and_compose(  # noqa: PLR0913
        self,
        form_turn: _FormTurn,
        checked: ValidSubmission,
        action: ActionSchema,
        *,
        intent: Intent,
        provider: ProviderCapability,
        redacted: _Redacted,
        session: Session,
    ) -> _Reply:
        response = await self._call_provider_action(
            form_turn,
            checked,
            action,
            provider=provider,
            visibility=redacted.visibility,
        )
        if isinstance(response, Failure):
            _warn_if_auth_refused(response, form_turn.ctx)
            return await self._end(
                end_for_failed_call(
                    form_turn.turn.input,
                    response,
                    self._clarification_text,
                    action=action,
                ),
                form_turn,
            )
        # Before the answer streams: a closed screen must still leave it done.
        session.update_forms(
            with_call_filed(session.forms, checked, form_turn.turn.input)
        )
        return self._stream_provider_answer(response, intent, redacted, form_turn)

    async def _handle_init_response(
        self, form_turn: _FormTurn, session: Session
    ) -> _Reply:
        await self._redact_for_the_record(form_turn, ())
        return await self._end(
            end_for_no_init_pending(self._clarification_text), form_turn
        )

    def _stream_provider_answer(
        self,
        response: DiscoveredAnswer,
        intent: Intent,
        redacted: _Redacted,
        form_turn: _FormTurn,
    ) -> AsyncGenerator[TurnEvent]:
        evidence = assemble_evidence([(FORM_ASK_INDEX, response)], intent=intent)
        return self._stream_answer(evidence, redacted, form_turn)

    async def _stream_answer(
        self, evidence: Evidence, redacted: _Redacted, form_turn: _FormTurn
    ) -> AsyncGenerator[TurnEvent]:
        # The record only ever sees the tagged turn.
        recorded = replace(form_turn, turn=redacted.turn)
        # `aclosing` each layer: closing an async generator does not reach the
        # one it reads from, and a closed screen must close the model stream.
        async with (
            aclosing(
                compose_answer(
                    evidence,
                    turn=redacted.turn,
                    compose=self._components.compose,
                    localize=self._components.localize,
                    question=None,
                    ctx=form_turn.ctx,
                    recorder=form_turn.recorder,
                )
            ) as core_events,
            aclosing(
                reveal_to_farmer(
                    core_events,
                    visibility=redacted.visibility,
                    recorder=form_turn.recorder,
                    on_finished=partial(self._record, form_turn=recorded),
                )
            ) as farmer_events,
        ):
            async for event in farmer_events:
                yield event

    async def _send_init(
        self,
        form_turn: _FormTurn,
        details: ValidSubmission,
        *,
        provider: ProviderCapability,
        visibility: Visibility,
        session: Session,
    ) -> TurnFinished:
        submission = form_turn.turn.input
        init = find_init_call(details, self._forms)
        if not isinstance(init, InitCall):
            return await self._end(
                end_for_failed_init(
                    init, details=submission, text=self._clarification_text
                ),
                form_turn,
            )
        reply = await self._call_provider_action(
            form_turn, details, init.action, provider=provider, visibility=visibility
        )
        failure = find_init_failure(reply)
        if failure is not None:
            _warn_if_auth_refused(failure, form_turn.ctx)
            return await self._end(
                end_for_failed_init(
                    failure, details=submission, text=self._clarification_text
                ),
                form_turn,
            )
        return await self._save_and_end(
            settle_sent_init(
                session.forms,
                details,
                submission,
                provider=provider,
                init=init,
                text=self._clarification_text,
            ),
            session,
            form_turn,
        )

    async def _save_and_end(
        self, settled: FlowSettled, session: Session, form_turn: _FormTurn
    ) -> TurnFinished:
        session.update_forms(settled.forms)
        return await self._end(settled.ending, form_turn)

    async def _end(self, ending: FlowEnding, form_turn: _FormTurn) -> TurnFinished:
        outcome = outcome_for(
            ending.status,
            ending.cause,
            retry_after_seconds=ending.retry_after_seconds,
        )
        return await self._finish(outcome, ending.answer, form_turn)

    async def _call_provider_action(
        self,
        form_turn: _FormTurn,
        submission: ValidSubmission,
        action: ActionSchema,
        *,
        provider: ProviderCapability,
        visibility: Visibility,
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

    def _intent_for(self, context: FormContext) -> Intent | None:
        scheme = scheme_with_code(self._scheme_catalog.schemes(), context.scheme)
        if scheme is None:
            scheme = Scheme(code=context.scheme, name=context.scheme)
        ask = ask_for_form(context, scheme=scheme)
        if ask is None:
            return None
        return Intent(asks=(ask,), confidence=1.0)

    async def _find_form_provider(
        self, intent: Intent, form_turn: _FormTurn
    ) -> ProviderCapability | None:
        discovery = await self._find_providers(
            intent, form_turn, scheme_code=form_turn.turn.input.context.scheme
        )
        # form's action needs a provider to call and not a direct answer
        providers = discovery.capabilities.get(FORM_ASK_INDEX)
        return providers[0] if providers else None

    async def _find_providers(
        self,
        intent: Intent,
        form_turn: _FormTurn,
        *,
        scheme_code: str,
    ) -> DiscoveryResult:
        with trace_component(Stage.DISCOVERY, form_turn.ctx.trace_id):
            found = await self._components.discover(
                intent, form_turn.turn, now=datetime.now(UTC)
            )
            providers = discovery_for_scheme(found, scheme_code)
            set_current_span_attributes(
                providers_found=_count(found),
                providers_for_scheme=_count(providers),
            )
            set_current_span_content(
                input=asks_text(intent.asks), output=discovery_text(providers)
            )
        return providers

    async def _redact_for_the_record(
        self, form_turn: _FormTurn, free_text: Sequence[str]
    ) -> _Redacted:
        turn, ctx = form_turn.turn, form_turn.ctx
        surroundings = surrounding_texts_of(turn)
        with trace_component(Stage.REDACTION, ctx.trace_id):
            redaction = await self._components.redact(
                [*surroundings, *free_text], request_id=turn.transaction_id
            )
        form_turn.recorder.redacted(found=redaction.found, failed=redaction.failed)
        tagged_surroundings = redaction.texts[: len(surroundings)]
        tagged_free_text = redaction.texts[len(surroundings) :]
        redacted = _Redacted(
            turn=_with_values_withheld(
                with_surrounding_texts(turn, tagged_surroundings)
            ),
            free_text=_FREE_TEXT_SEPARATOR.join(tagged_free_text),
            visibility=redaction.visibility,
        )
        self._turns.opened(ctx, redacted.turn)
        return redacted

    async def _moderate(
        self, redacted: _Redacted, form_turn: _FormTurn
    ) -> ModerationDecision:
        decision = ModerationDecision(outcome=Outcome.PROCEED)
        if redacted.free_text:
            with trace_component(Stage.MODERATION, form_turn.ctx.trace_id):
                decision = await moderate(
                    ModerationContext.for_text(redacted.turn, redacted.free_text),
                    self._policies,
                    self._moderation_llm,
                    self._prompts,
                )
                set_current_span_attributes(**moderation_attributes(decision))
                set_current_span_content(input=redacted.free_text)
        note_stage(self._telemetry, "moderation", form_turn.ctx, decision.outcome.value)
        return decision

    async def _refuse(
        self, decision: ModerationDecision, form_turn: _FormTurn
    ) -> TurnFinished:
        # On _finish, not _end: TextPath shares refusal_for, which builds the outcome.
        outcome, refusal = refusal_for(decision)
        return await self._finish(outcome, refusal, form_turn)

    async def _finish(
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
    checked: ValidSubmission | FormErrors | UnknownForm,
) -> tuple[str, ...]:
    return free_text_of(checked) if isinstance(checked, ValidSubmission) else ()


def _warn_if_auth_refused(failure: Failure | NoInitChallenge, ctx: TurnContext) -> None:
    if isinstance(failure, Failure) and is_auth_refused(failure):
        warn_auth_refused(failure, request_id=ctx.trace_id)


def _count(discovery: DiscoveryResult) -> int:
    return sum(len(candidates) for candidates in discovery.capabilities.values())


def _with_values_withheld(
    turn: UserTurn[FormSubmission],
) -> UserTurn[FormSubmission]:
    return turn.model_copy(update={"input": withhold_values(turn.input)})


def _reference_line(submission: FormSubmission) -> str:
    names = ", ".join(value.name for value in submission.values)
    return f"form {submission.form_id} ({submission.context}): {names}"
