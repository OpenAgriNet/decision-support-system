"""The path for a submitted form (#205): check the values, moderate the
complaint, then file them.

TODO(#205): discover the desk, file through the planner's `support` tool,
compose the ticket reply.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass

from dss.adapters.observability.tracing import (
    TurnRecorder,
    set_current_span_attributes,
    set_current_span_content,
)
from dss.core.channel.localize import LocalizeText, localize_answer
from dss.core.channel.models import ClarificationText, ComposedAnswer
from dss.core.forms.models import FormErrors, FormSubmission, ValidSubmission
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    answer_for_form_errors,
    free_text_of,
    validate_submission,
    withhold_values,
)
from dss.core.moderation.models import ModerationContext, ModerationDecision, Outcome
from dss.core.moderation.service import moderate
from dss.core.policy.models import Policy
from dss.core.redaction.turn import surrounding_texts_of, with_surrounding_texts
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
from dss.orchestration.orchestrator import (
    build_turn_finished,
    note_stage,
    outcome_for,
    refusal_for,
    spoken_text,
)
from dss.orchestration.redaction import RedactTexts
from dss.orchestration.turn import moderation_attributes
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider
from dss.ports.sinks import TelemetrySink, TurnSink

# Several prose fields are judged as one text, like one question.
_FREE_TEXT_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class _Redacted:
    """The turn as the record and the models may see it: history and phone
    tagged, form values withheld, and the complaint tagged on its own."""

    turn: UserTurn[FormSubmission]
    free_text: str


class FormPath:
    def __init__(  # noqa: PLR0913
        self,
        *,
        redact: RedactTexts,
        moderation_llm: LLMProvider,
        prompts: PromptProvider,
        policies: Sequence[Policy],
        forms: FormLookup,
        turns: TurnSink,
        telemetry: TelemetrySink,
        clarification_text: ClarificationText,
        localize: LocalizeText,
    ) -> None:
        self._redact = redact
        self._moderation_llm = moderation_llm
        self._prompts = prompts
        self._policies = policies
        self._forms = forms
        self._turns = turns
        self._telemetry = telemetry
        self._clarification_text = clarification_text
        self._localize = localize

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
        raise NotImplementedError("TODO(#205): file the form with the desk")

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
            redaction = await self._redact(
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
        answer = await localize_answer(answer, self._localize, turn=turn)
        finished = build_turn_finished(outcome, answer)
        self._turns.closed(ctx, finished)
        recorder.status(finished.outcome.status.value)
        recorder.content(
            query=_reference_line(turn.input), answer=spoken_text(finished.content)
        )
        return finished


def _with_values_withheld(
    turn: UserTurn[FormSubmission],
) -> UserTurn[FormSubmission]:
    return turn.model_copy(update={"input": withhold_values(turn.input)})


def _reference_line(submission: FormSubmission) -> str:
    names = ", ".join(value.name for value in submission.values)
    return f"form {submission.form_id} ({submission.context}): {names}"
