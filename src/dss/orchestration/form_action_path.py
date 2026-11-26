"""A button pressed on an offered form. Today only Resend OTP: init is called
again with the details already waiting, at the provider that took them.

No moderation, discovery or composer: the press carries no words, and the
waiting entry already names the provider."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import assert_never

from dss.adapters.observability.tracing import (
    TurnRecorder,
    set_current_span_attributes,
)
from dss.core.channel.localize import LocalizeText
from dss.core.channel.models import ClarificationText
from dss.core.forms.challenge import InitReplyWithoutChallenge
from dss.core.forms.init_flow import (
    FlowDecision,
    InitCall,
    NoInitPending,
    WaitingFlow,
    decide_after_resent_init,
    decide_with_no_waiting_flow,
    end_for_failed_init,
    end_for_failed_resend,
    find_init_call,
    find_waiting_flow,
    offers_action,
    read_init_reply,
)
from dss.core.forms.models import (
    FlowEnding,
    FormAction,
    FormActionName,
    FormContext,
)
from dss.core.forms.registry import FormLookup
from dss.core.forms.service import mask_pii_fields
from dss.core.intent.models import Ask
from dss.core.planner.models import Failure
from dss.core.redaction.visibility import Visibility
from dss.core.shared.models import TurnContext, TurnEvent, TurnFinished, UserTurn
from dss.orchestration.form_turn import (
    FormTurn,
    FormTurnCloser,
    ask_for_form_context,
    call_provider_action,
    redact_texts,
    warn_if_auth_refused,
    warn_if_unknown_challenge,
)
from dss.orchestration.redaction import RedactTexts
from dss.orchestration.session import Session
from dss.ports.invocation import ProviderActionInvocation
from dss.ports.scheme_catalog import SchemeCatalog
from dss.ports.sinks import TurnSink

type _ActionTurn = FormTurn[FormAction]


class FormActionPath:
    def __init__(  # noqa: PLR0913
        self,
        *,
        forms: FormLookup,
        scheme_catalog: SchemeCatalog,
        redact: RedactTexts,
        localize: LocalizeText,
        invocation_for: Callable[[UserTurn], ProviderActionInvocation],
        turns: TurnSink,
        clarification_text: ClarificationText,
    ) -> None:
        self._forms = forms
        self._scheme_catalog = scheme_catalog
        self._redact = redact
        self._invocation_for = invocation_for
        self._turns = turns
        self._clarification_text = clarification_text
        self._closer = FormTurnCloser(localize=localize, turns=turns)

    async def run(
        self,
        turn: UserTurn[FormAction],
        *,
        ctx: TurnContext,
        recorder: TurnRecorder,
        session: Session,
    ) -> AsyncGenerator[TurnEvent]:
        action_turn = FormTurn(turn=turn, ctx=ctx, recorder=recorder)
        set_current_span_attributes(
            form_action=turn.input.name.value, action_id=turn.input.action_id
        )
        # Every exit writes the record: opened before any check.
        texts = await redact_texts(action_turn, (), redact=self._redact)
        self._turns.opened(ctx, texts.turn)
        match turn.input.name:
            case FormActionName.RESEND_INIT:
                yield await self._resend_init(action_turn, texts.visibility, session)
            case unknown:
                assert_never(unknown)

    async def _resend_init(
        self, action_turn: _ActionTurn, visibility: Visibility, session: Session
    ) -> TurnFinished:
        flow = find_waiting_flow(
            session.forms,
            action_turn.turn.input.context,
            self._forms,
            ask_for=self._ask_for,
        )
        # A button the form never offered, e.g. Resend for a method we do
        # not know, has nothing to resend: no SMS goes out.
        if isinstance(flow, WaitingFlow) and not offers_action(
            flow, action_turn.turn.input.name
        ):
            flow = NoInitPending()
        if not isinstance(flow, WaitingFlow):
            return await self._update_forms_and_end(
                decide_with_no_waiting_flow(
                    session.forms, flow, self._clarification_text
                ),
                session,
                action_turn,
            )
        init = find_init_call(flow.details, self._forms)
        if not isinstance(init, InitCall):
            return await self._finish(
                end_for_failed_init(
                    init, details=flow.entry.submission, text=self._clarification_text
                ),
                action_turn,
            )
        return await self._send_init_again(
            action_turn, flow, init, visibility=visibility, session=session
        )

    async def _send_init_again(
        self,
        action_turn: _ActionTurn,
        flow: WaitingFlow,
        init: InitCall,
        *,
        visibility: Visibility,
        session: Session,
    ) -> TurnFinished:
        # The saved details are kept as typed: masked here, so traces see tags.
        details = mask_pii_fields(flow.details)
        reply = await call_provider_action(
            action_turn,
            details.submission.values,
            init.action,
            provider=flow.entry.provider,
            invocation=self._invocation_for(action_turn.turn),
            visibility=visibility.merged_with(details.visibility),
        )
        init_reply = read_init_reply(reply)
        warn_if_unknown_challenge(init_reply)
        form_id = action_turn.turn.input.form_id
        if isinstance(init_reply, Failure | InitReplyWithoutChallenge):
            warn_if_auth_refused(init_reply, action_turn.ctx)
            return await self._finish(
                end_for_failed_resend(
                    init_reply,
                    flow,
                    form_id=form_id,
                    init_form=init.init_form,
                    text=self._clarification_text,
                ),
                action_turn,
            )
        return await self._update_forms_and_end(
            decide_after_resent_init(
                session.forms,
                flow,
                form_id=form_id,
                init_form=init.init_form,
                challenge=init_reply,
                text=self._clarification_text,
                now=datetime.now(UTC),
            ),
            session,
            action_turn,
        )

    async def _update_forms_and_end(
        self, decision: FlowDecision, session: Session, action_turn: _ActionTurn
    ) -> TurnFinished:
        session.update_forms(decision.forms)
        return await self._finish(decision.ending, action_turn)

    async def _finish(
        self, ending: FlowEnding, action_turn: _ActionTurn
    ) -> TurnFinished:
        return await self._closer.finish(
            ending, action_turn, query=_reference_line(action_turn.turn.input)
        )

    def _ask_for(self, context: FormContext) -> Ask | None:
        return ask_for_form_context(context, self._scheme_catalog)


def _reference_line(action: FormAction) -> str:
    return (
        f"form action {action.name} {action.action_id} on {action.form_id}"
        f" ({action.context})"
    )
