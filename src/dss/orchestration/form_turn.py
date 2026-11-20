"""Steps a turn on a form takes, whether the farmer sent the form or pressed a
button on it."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dss.adapters.observability.tracing import (
    TurnRecorder,
    set_current_span_attributes,
    set_current_span_content,
)
from dss.core.channel.localize import LocalizeText, localize_answer
from dss.core.channel.models import ComposedAnswer
from dss.core.forms.challenge import IssuedChallenge
from dss.core.forms.init_flow import InitReplyWithoutChallenge
from dss.core.forms.models import ActionSchema, FlowEnding, FormContext
from dss.core.forms.service import is_auth_refused
from dss.core.intent.models import Ask, Scheme
from dss.core.intent.service import scheme_with_code
from dss.core.planner.models import Failure
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.core.redaction.turn import surrounding_texts_of, with_surrounding_texts
from dss.core.redaction.visibility import Visibility
from dss.core.shared.models import (
    TurnContext,
    TurnFinished,
    TurnInput,
    TurnOutcome,
    UserTurn,
)
from dss.observability.stages import Stage
from dss.observability.trace_log import trace_component
from dss.orchestration.form_offer import ask_for_form
from dss.orchestration.orchestrator import build_turn_finished, outcome_for, spoken_text
from dss.orchestration.provider_action import call_provider_action, warn_auth_refused
from dss.orchestration.redaction import RedactTexts
from dss.orchestration.stage_content import provider_response_text
from dss.ports.invocation import ProviderActionInvocation
from dss.ports.scheme_catalog import SchemeCatalog
from dss.ports.sinks import TurnSink

logger = logging.getLogger(__name__)

# Several prose fields are judged as one text, like one question.
_FREE_TEXT_SEPARATOR = "\n\n"


# Which turn this is; the session is apart, since it changes during the turn.
@dataclass(frozen=True)
class FormTurn[InputT: TurnInput]:
    turn: UserTurn[InputT]
    ctx: TurnContext
    recorder: TurnRecorder


@dataclass(frozen=True)
class RedactedTexts[InputT: TurnInput]:
    turn: UserTurn[InputT]
    free_text: str
    visibility: Visibility


async def redact_texts[InputT: TurnInput](
    form_turn: FormTurn[InputT],
    free_text: Sequence[str],
    *,
    redact: RedactTexts,
) -> RedactedTexts[InputT]:
    """The history, the phone and the free text, tagged. The input itself is
    the caller's to withhold."""

    turn, ctx = form_turn.turn, form_turn.ctx
    surroundings = surrounding_texts_of(turn)
    with trace_component(Stage.REDACTION, ctx.trace_id):
        redaction = await redact(
            [*surroundings, *free_text], transaction_id=turn.transaction_id
        )
    form_turn.recorder.redacted(found=redaction.found, failed=redaction.failed)
    tagged_surroundings = redaction.texts[: len(surroundings)]
    tagged_free_text = redaction.texts[len(surroundings) :]
    return RedactedTexts(
        turn=with_surrounding_texts(turn, tagged_surroundings),
        free_text=_FREE_TEXT_SEPARATOR.join(tagged_free_text),
        visibility=redaction.visibility,
    )


async def call_traced_provider_action(  # noqa: PLR0913
    form_turn: FormTurn,
    values: Mapping[str, str],
    action: ActionSchema,
    *,
    provider: ProviderCapability,
    invocation: ProviderActionInvocation,
    visibility: Visibility,
    init_values: Mapping[str, str] | None = None,
) -> DiscoveredAnswer | Failure:
    with trace_component(Stage.PROVIDER_ACTION, form_turn.ctx.trace_id):
        response = await call_provider_action(
            provider,
            action,
            values,
            invocation=invocation,
            transaction_id=form_turn.turn.transaction_id,
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


def warn_if_auth_refused(
    failure: Failure | InitReplyWithoutChallenge, ctx: TurnContext
) -> None:
    if isinstance(failure, Failure) and is_auth_refused(failure):
        warn_auth_refused(failure, request_id=ctx.trace_id)


# Such a challenge is still answered, but offers no Resend: say why.
def warn_if_unknown_challenge(
    init_reply: IssuedChallenge | Failure | InitReplyWithoutChallenge,
) -> None:
    if isinstance(init_reply, IssuedChallenge) and init_reply.unknown_method:
        logger.warning(
            "init challenge method not known",
            extra={"challenge_method": init_reply.unknown_method},
        )


def ask_for_form_context(context: FormContext, schemes: SchemeCatalog) -> Ask | None:
    scheme = scheme_with_code(schemes.schemes(), context.scheme)
    if scheme is None:
        scheme = Scheme(code=context.scheme, name=context.scheme)
    return ask_for_form(context, scheme=scheme)


@dataclass(frozen=True)
class FormTurnCloser:
    """Ends a form turn with a fixed answer, and writes its record."""

    localize: LocalizeText
    turns: TurnSink

    async def finish(
        self, ending: FlowEnding, form_turn: FormTurn, *, query: str
    ) -> TurnFinished:
        outcome = outcome_for(
            ending.status,
            ending.cause,
            retry_after_seconds=ending.retry_after_seconds,
        )
        return await self.finish_with_outcome(
            outcome, ending.answer, form_turn, query=query
        )

    async def finish_with_outcome(
        self,
        outcome: TurnOutcome,
        answer: ComposedAnswer,
        form_turn: FormTurn,
        *,
        query: str,
    ) -> TurnFinished:
        answer = await localize_answer(answer, self.localize, turn=form_turn.turn)
        finished = build_turn_finished(outcome, answer)
        self.record(finished, form_turn=form_turn, query=query)
        return finished

    def record(
        self, finished: TurnFinished, *, form_turn: FormTurn, query: str
    ) -> None:
        self.turns.closed(form_turn.ctx, finished)
        form_turn.recorder.status(finished.outcome.status.value)
        form_turn.recorder.content(query=query, answer=spoken_text(finished.content))
