"""The path for a submitted form (#205): check the values, then file them.

Only the check and its exit exist yet. TODO(#205): moderate the free text,
discover the desk, file through the planner's `support` tool, compose the
ticket reply.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from dss.adapters.observability.tracing import TurnRecorder
from dss.core.channel.localize import LocalizeText, localize_answer
from dss.core.channel.models import ClarificationText
from dss.core.forms.models import FormErrors, FormSubmission
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import (
    answer_for_form_errors,
    validate_submission,
    withhold_values,
)
from dss.core.redaction.turn import surrounding_texts_of, with_surrounding_texts
from dss.core.shared.models import (
    Cause,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnStatus,
    UserTurn,
)
from dss.observability.stages import Stage
from dss.observability.trace_log import trace_component
from dss.orchestration.orchestrator import (
    build_turn_finished,
    outcome_for,
    spoken_text,
)
from dss.orchestration.redaction import RedactTexts
from dss.ports.sinks import TurnSink


class FormPath:
    def __init__(
        self,
        *,
        redact: RedactTexts,
        forms: FormLookup,
        turns: TurnSink,
        clarification_text: ClarificationText,
        localize: LocalizeText,
    ) -> None:
        self._redact = redact
        self._forms = forms
        self._turns = turns
        self._clarification_text = clarification_text
        self._localize = localize

    async def run(
        self,
        turn: UserTurn[FormSubmission],
        *,
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> AsyncGenerator[TurnEvent]:
        # The history and the phone are tagged as on the text path. The form's
        # values are not tagged but withheld: the record and the trace hold
        # the form's references and field names, and a value leaves the turn
        # only inside the provider call.
        with trace_component(Stage.REDACTION, ctx.trace_id):
            redaction = await self._redact(
                surrounding_texts_of(turn), request_id=turn.transaction_id
            )
        recorder.redacted(found=redaction.found, failed=redaction.failed)
        recorded_turn = with_surrounding_texts(turn, redaction.texts)
        self._turns.opened(ctx, _with_values_withheld(recorded_turn))

        checked = validate_submission(turn.input, self._forms)
        if isinstance(checked, FormErrors):
            yield await self._hand_back(checked, turn=turn, ctx=ctx, recorder=recorder)
            return
        raise NotImplementedError("TODO(#205): file the form with the desk")

    async def _hand_back(
        self,
        errors: FormErrors,
        *,
        turn: UserTurn[FormSubmission],
        ctx: TurnContext,
        recorder: TurnRecorder,
    ) -> TurnFinished:
        answer = answer_for_form_errors(errors, self._clarification_text)
        answer = await localize_answer(answer, self._localize, turn=turn)
        finished = build_turn_finished(
            outcome_for(TurnStatus.REQUIRES_INPUT, Cause.INVALID_INPUT), answer
        )
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
