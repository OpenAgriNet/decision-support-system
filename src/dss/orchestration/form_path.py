"""The path for a submitted form (#205): check the values, moderate the
complaint, find the provider, file the form through the planner, and tell
the farmer the ticket, or that the form is still theirs."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial

from dss.adapters.observability.tracing import (
    TurnRecorder,
    set_current_span_attributes,
    set_current_span_content,
)
from dss.core.channel.localize import localize_answer
from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.channel.service import answer_for_unserved_asks
from dss.core.enrichment.service import scheme_with_code
from dss.core.forms.models import (
    Filing,
    FormContext,
    FormErrors,
    FormSubmission,
    ValidSubmission,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    answer_for_failed_filing,
    answer_for_form_errors,
    answer_for_unknown_form,
    candidates_for_scheme,
    cause_for_unfiled,
    free_text_of,
    validate_submission,
    withhold_values,
)
from dss.core.intent.models import Intent, Scheme
from dss.core.moderation.models import ModerationContext, ModerationDecision, Outcome
from dss.core.moderation.service import moderate
from dss.core.planner.models import Evidence, Failure, Verdict
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
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
from dss.observability.stages import Stage
from dss.observability.trace_log import trace_component
from dss.orchestration.form_offer import ask_for_form
from dss.orchestration.orchestrator import (
    Components,
    build_turn_finished,
    compose_answer,
    note_stage,
    outcome_for,
    refusal_for,
    reveal_to_farmer,
    spoken_text,
)
from dss.orchestration.stage_content import asks_text, discovery_text, evidence_text
from dss.orchestration.turn import moderation_attributes
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider
from dss.ports.scheme_catalog import SchemeCatalog
from dss.ports.sinks import TelemetrySink, TurnSink

# Several prose fields are judged as one text, like one question.
_FREE_TEXT_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class _Redacted:
    """The turn as the record and the models may see it: history and phone
    tagged, form values withheld, and the complaint tagged on its own."""

    turn: UserTurn[FormSubmission]
    free_text: str
    # The real values behind the tags, for the provider call only.
    visibility: Visibility


class FormPath:
    def __init__(  # noqa: PLR0913
        self,
        *,
        moderation_llm: LLMProvider,
        prompts: PromptProvider,
        policies: Sequence[Policy],
        forms: FormLookup,
        scheme_catalog: SchemeCatalog,
        components: Components,
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
    ) -> AsyncGenerator[TurnEvent]:
        # Raw values are checked first: a tagged phone fails its own pattern.
        checked = validate_submission(turn.input, self._forms)
        redacted = await self._redact_for_the_record(turn, checked, ctx, recorder)
        self._turns.opened(ctx, redacted.turn)

        if isinstance(checked, FormErrors):
            yield await self._hand_back(checked, turn=turn, ctx=ctx, recorder=recorder)
            return

        decision = await self._moderate(redacted, ctx)
        note_stage(self._telemetry, "moderation", ctx, decision.outcome.value)
        if decision.outcome is not Outcome.PROCEED:
            outcome, refusal = refusal_for(decision)
            yield await self._finish(
                outcome, refusal, turn=turn, ctx=ctx, recorder=recorder
            )
            return

        intent = self._intent_for(turn.input.context)
        action = self._forms.action_schema(checked.definition.context)
        if intent is None or action is None:
            yield await self._hand_back_unknown(turn, ctx=ctx, recorder=recorder)
            return
        discovery = await self._find_providers(
            intent, turn, ctx, scheme_code=turn.input.context.scheme
        )
        # Not `serves(0)`: a catalog's Direct answer counts there, and filing
        # needs a provider to call.
        if not discovery.capabilities.get(0):
            answer = answer_for_unserved_asks(intent.asks, self._clarification_text)
            yield await self._finish(
                outcome_for(TurnStatus.NO_MATCH),
                answer,
                turn=turn,
                ctx=ctx,
                recorder=recorder,
            )
            return

        evidence = await self._file(
            turn,
            Filing(submission=checked, action=action),
            intent=intent,
            discovery=discovery,
            decision=decision,
            visibility=redacted.visibility,
            ctx=ctx,
        )
        if not evidence.has_any_result:
            yield await self._hand_back_unfiled(
                _filing_failure(evidence), turn=turn, ctx=ctx, recorder=recorder
            )
            return
        async with aclosing(
            self._confirm_the_filing(
                evidence,
                turn=redacted.turn,
                visibility=redacted.visibility,
                ctx=ctx,
                recorder=recorder,
            )
        ) as events:
            async for event in events:
                yield event

    async def _confirm_the_filing(
        self,
        evidence: Evidence,
        *,
        turn: UserTurn[FormSubmission],
        visibility: Visibility,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> AsyncGenerator[TurnEvent]:
        """The composer's confirmation, streamed as it is written, with the
        farmer's own values in place of any tag. ``turn`` is the redacted
        one: the model sees tags and withheld values, never the originals."""

        # `aclosing` each layer: closing an async generator does not reach the
        # one it reads from, and a closed screen must close the model stream.
        async with (
            aclosing(
                compose_answer(
                    evidence,
                    turn=turn,
                    components=self._components,
                    question=None,
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

    async def _hand_back_unfiled(
        self,
        failure: Failure | None,
        *,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> TurnFinished:
        """The form stays the farmer's: `unavailable`, with the provider's own
        time to wait if it named one. No invented delay, and no provider
        text."""

        cause = cause_for_unfiled(failure)
        answer = answer_for_failed_filing(turn.input, cause, self._clarification_text)
        retry_after = failure.retry_after_seconds if failure is not None else None
        outcome = outcome_for(TurnStatus.UNAVAILABLE, cause).model_copy(
            update={"retry_after_seconds": retry_after}
        )
        return await self._finish(
            outcome, answer, turn=turn, ctx=ctx, recorder=recorder
        )

    async def _file(  # noqa: PLR0913
        self,
        turn: UserTurn[FormSubmission],
        filing: Filing,
        *,
        intent: Intent,
        discovery: DiscoveryResult,
        decision: ModerationDecision,
        visibility: Visibility,
        ctx: TurnContext,
    ) -> Evidence:
        """Past the barrier: the decision cleared, so the planner's `support`
        tool may call the provider. The `Verdict` is how that clearance
        reaches the tool."""

        verdict = Verdict()
        verdict.set(decision)
        with trace_component(Stage.PLANNER, ctx.trace_id):
            evidence = await self._components.plan(
                turn,
                intent=intent,
                discovery=discovery,
                verdict=verdict,
                visibility=visibility,
                filing=filing,
            )
            set_current_span_content(
                input=asks_text(intent.asks), output=evidence_text(evidence)
            )
        return evidence

    def _intent_for(self, context: FormContext) -> Intent | None:
        """One ask, as the offer path would have classified it, or ``None``
        for a form no ask maps to. The scheme comes from the catalog by code;
        a code the catalog does not list is still the code the provider is
        matched on."""

        scheme = scheme_with_code(self._scheme_catalog.aliases(), context.scheme)
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
        """Discovery as on any turn, then only the providers advertising the
        form's scheme: every grievance desk is indexed under one type, and
        nothing but this filter keeps a PMFBY complaint off a PM-KISAN desk."""

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
        checked: ValidSubmission | FormErrors,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> _Redacted:
        """One redaction call over everything a model or a sink may see. The
        history and the phone are tagged as on the text path; the form's
        values are withheld, except the complaint, which is tagged so it can
        be moderated. A value leaves the turn only inside the provider call."""

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
        """The complaint through the same policies as a question. Scope is
        not checked: that is intent's, which this path never runs. A form
        with nothing written in prose has nothing to judge."""

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
        """The turn sink is required, so a failure here is not caught:
        answering while silently failing to record is no success."""

        self._turns.closed(ctx, finished)
        recorder.status(finished.outcome.status.value)
        recorder.content(
            query=_reference_line(turn.input), answer=spoken_text(finished.content)
        )


def _filing_failure(evidence: Evidence) -> Failure | None:
    """The one ask's failure, when the desk was called and said no. ``None``
    when the planner never called it."""

    return next(
        (failure for ask in evidence.ask_evidences for failure in ask.failures),
        None,
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
