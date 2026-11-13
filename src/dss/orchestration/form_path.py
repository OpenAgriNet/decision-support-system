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
from dss.core.channel.service import answer_for_unserved_asks
from dss.core.forms.models import (
    ActionSchema,
    FormContext,
    FormErrors,
    FormSubmission,
    UnknownForm,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    answer_for_failed_call,
    answer_for_form_errors,
    answer_for_unknown_form,
    candidates_for_scheme,
    cause_for_failed_call,
    free_text_of,
    validate_submission,
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
    Cause,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnOutcome,
    TurnStatus,
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
from dss.orchestration.provider_action import FORM_ASK_INDEX, call_provider_action
from dss.orchestration.redaction import RedactTexts
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

    async def run(  # noqa: PLR0911 - one exit per way a form is not sent
        self,
        turn: UserTurn[FormSubmission],
        *,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> AsyncGenerator[TurnEvent]:
        # Raw values are checked first: a tagged phone fails its own pattern.
        checked: ValidSubmission | FormErrors | UnknownForm = validate_submission(
            turn.input, self._forms
        )
        redacted = await self._redact_for_the_record(turn, checked, ctx, recorder)
        self._turns.opened(ctx, redacted.turn)

        if isinstance(checked, FormErrors):
            yield await self._hand_back(checked, turn=turn, ctx=ctx, recorder=recorder)
            return
        if isinstance(checked, UnknownForm):
            yield await self._hand_back_unknown(turn, ctx=ctx, recorder=recorder)
            return

        # Before moderation: a form that cannot be sent is not worth a model call.
        intent = self._intent_for(turn.input.context)
        if intent is None:  # a known form, but not an action the DSS can file
            yield await self._hand_back_unknown(turn, ctx=ctx, recorder=recorder)
            return

        action = self._forms.action_schema(turn.input.context)
        # shortcut: the registry builds each form from its schema, so this should
        # not happen; link the two so it cannot, then drop this branch.
        if action is None:
            yield await self._hand_back_unknown(turn, ctx=ctx, recorder=recorder)
            return

        decision = await self._moderate(redacted, ctx)
        note_stage(self._telemetry, "moderation", ctx, decision.outcome.value)

        if decision.outcome is not Outcome.PROCEED:
            outcome, refusal = refusal_for(decision)
            yield await self._finish(
                outcome, refusal, turn=turn, ctx=ctx, recorder=recorder
            )
            return

        discovery = await self._find_providers(
            intent, turn, ctx, scheme_code=turn.input.context.scheme
        )

        # form's action needs a provider to call and not a direct answer
        providers = discovery.capabilities.get(FORM_ASK_INDEX)
        if not providers:
            yield await self._finish(
                outcome_for(TurnStatus.NO_MATCH),
                answer_for_unserved_asks(intent.asks, self._clarification_text),
                turn=turn,
                ctx=ctx,
                recorder=recorder,
            )
            return

        response = await self._call_provider_action(
            turn,
            checked,
            action,
            provider=providers[0],
            visibility=redacted.visibility,
            ctx=ctx,
        )
        if isinstance(response, Failure):
            yield await self._hand_back_failed_call(
                response, turn=turn, ctx=ctx, recorder=recorder
            )
            return
        async with aclosing(
            self._stream_answer(
                assemble_evidence([(FORM_ASK_INDEX, response)], intent=intent),
                turn=redacted.turn,
                visibility=redacted.visibility,
                ctx=ctx,
                recorder=recorder,
            )
        ) as events:
            async for event in events:
                yield event

    async def _stream_answer(
        self,
        evidence: Evidence,
        *,
        turn: UserTurn[FormSubmission],
        visibility: Visibility,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> AsyncGenerator[TurnEvent]:
        # `aclosing` each layer: closing an async generator does not reach the
        # one it reads from, and a closed screen must close the model stream.
        async with (
            aclosing(
                compose_answer(
                    evidence,
                    turn=turn,
                    compose=self._components.compose,
                    localize=self._components.localize,
                    ctx=ctx,
                    recorder=recorder,
                )
            ) as core_events,
            aclosing(
                reveal_to_farmer(
                    core_events,
                    visibility=visibility,
                    recorder=recorder,
                    on_finished=partial(
                        self._record, turn=turn, ctx=ctx, recorder=recorder
                    ),
                )
            ) as farmer_events,
        ):
            async for event in farmer_events:
                yield event

    async def _hand_back_failed_call(
        self,
        failure: Failure,
        *,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> TurnFinished:
        cause = cause_for_failed_call(failure)
        answer = answer_for_failed_call(turn.input, cause, self._clarification_text)
        outcome = outcome_for(TurnStatus.UNAVAILABLE, cause).model_copy(
            update={"retry_after_seconds": failure.retry_after_seconds}
        )
        return await self._finish(
            outcome, answer, turn=turn, ctx=ctx, recorder=recorder
        )

    async def _call_provider_action(  # noqa: PLR0913
        self,
        turn: UserTurn[FormSubmission],
        submission: ValidSubmission,
        action: ActionSchema,
        *,
        provider: ProviderCapability,
        visibility: Visibility,
        ctx: TurnContext,
    ) -> DiscoveredAnswer | Failure:
        with trace_component(Stage.PROVIDER_ACTION, ctx.trace_id):
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

    async def _find_providers(
        self,
        intent: Intent,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        *,
        scheme_code: str,
    ) -> DiscoveryResult:
        with trace_component(Stage.DISCOVERY, ctx.trace_id):
            found = await self._components.discover(intent, turn, now=datetime.now(UTC))
            providers = replace(
                found,
                capabilities={
                    position: candidates_for_scheme(candidates, scheme_code)
                    for position, candidates in found.capabilities.items()
                },
            )
            set_current_span_attributes(
                providers_found=_count(found),
                providers_for_scheme=_count(providers),
            )
            set_current_span_content(
                input=asks_text(intent.asks), output=discovery_text(providers)
            )
        return providers

    async def _redact_for_the_record(
        self,
        turn: UserTurn[FormSubmission],
        checked: ValidSubmission | FormErrors | UnknownForm,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> _Redacted:
        surroundings = surrounding_texts_of(turn)
        free_text = (
            free_text_of(checked) if isinstance(checked, ValidSubmission) else ()
        )
        with trace_component(Stage.REDACTION, ctx.trace_id):
            redaction = await self._components.redact(
                [*surroundings, *free_text], request_id=turn.transaction_id
            )
        recorder.redacted(found=redaction.found, failed=redaction.failed)
        tagged_surroundings = redaction.texts[: len(surroundings)]
        tagged_free_text = redaction.texts[len(surroundings) :]
        return _Redacted(
            turn=_with_values_withheld(
                with_surrounding_texts(turn, tagged_surroundings)
            ),
            free_text=_FREE_TEXT_SEPARATOR.join(tagged_free_text),
            visibility=redaction.visibility,
        )

    async def _moderate(
        self, redacted: _Redacted, ctx: TurnContext
    ) -> ModerationDecision:
        if not redacted.free_text:
            return ModerationDecision(outcome=Outcome.PROCEED)
        with trace_component(Stage.MODERATION, ctx.trace_id):
            decision = await moderate(
                ModerationContext.for_text(redacted.turn, redacted.free_text),
                self._policies,
                self._moderation_llm,
                self._prompts,
            )
            set_current_span_attributes(**moderation_attributes(decision))
            set_current_span_content(input=redacted.free_text)
        return decision

    async def _hand_back_unknown(
        self,
        turn: UserTurn[FormSubmission],
        *,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> TurnFinished:
        answer = answer_for_unknown_form(self._clarification_text)
        outcome = outcome_for(TurnStatus.REQUIRES_INPUT, Cause.INVALID_INPUT)
        return await self._finish(
            outcome, answer, turn=turn, ctx=ctx, recorder=recorder
        )

    async def _hand_back(
        self,
        errors: FormErrors,
        *,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> TurnFinished:
        answer = answer_for_form_errors(errors, self._clarification_text)
        outcome = outcome_for(TurnStatus.REQUIRES_INPUT, Cause.INVALID_INPUT)
        return await self._finish(
            outcome, answer, turn=turn, ctx=ctx, recorder=recorder
        )

    async def _finish(
        self,
        outcome: TurnOutcome,
        answer: ComposedAnswer,
        *,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> TurnFinished:
        answer = await localize_answer(answer, self._components.localize, turn=turn)
        finished = build_turn_finished(outcome, answer)
        self._record(finished, turn=turn, ctx=ctx, recorder=recorder)
        return finished

    def _record(
        self,
        finished: TurnFinished,
        *,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> None:
        self._turns.closed(ctx, finished)
        recorder.status(finished.outcome.status.value)
        recorder.content(
            query=_reference_line(turn.input), answer=spoken_text(finished.content)
        )


def _count(discovery: DiscoveryResult) -> int:
    return sum(len(candidates) for candidates in discovery.capabilities.values())


def _with_values_withheld(
    turn: UserTurn[FormSubmission],
) -> UserTurn[FormSubmission]:
    return turn.model_copy(update={"input": withhold_values(turn.input)})


def _reference_line(submission: FormSubmission) -> str:
    names = ", ".join(value.name for value in submission.values)
    return f"form {submission.form_id} ({submission.context}): {names}"
