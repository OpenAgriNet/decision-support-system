"""Tier 3 — the Resend OTP button, through the real orchestrator and paths."""

from __future__ import annotations

from dss.config.clarification_text_loader import load_clarification_text
from dss.core.forms.models import (
    FormAction,
    FormActionName,
    FormActionOffer,
    FormAwaitingInit,
    FormBlock,
    FormStatus,
)
from dss.core.shared.models import (
    Cause,
    ProviderAction,
    TurnStatus,
    UserTurn,
)
from tests.support.fakes import RecordingActionInvocation

from .test_form_path import (
    _CHALLENGE,
    _KISAN_PROVIDER,
    _KISAN_VALUES,
    _OTP,
    _PMKISAN_INIT,
    _TICKET,
    _build_kisan_orchestrator,
    _collect,
    _finished,
    _init_failed,
    _init_response_turn,
    _kisan_details_turn,
    _providers_found,
    _RecordingDiscovery,
    _save_waiting,
    _stored_state,
)

_TEXT = load_clarification_text()
_RESEND = FormActionOffer(name=FormActionName.RESEND_INIT, label="Resend OTP")


def _resend_turn(form_id: str = "otp-1") -> UserTurn:
    return UserTurn(
        input=FormAction(
            form_id=form_id,
            context=_PMKISAN_INIT,
            name=FormActionName.RESEND_INIT,
            action_id="press-1",
        ),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


async def test_resend_calls_init_again_with_the_saved_details_and_provider() -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    discovery = _RecordingDiscovery(_providers_found())
    orch, store, _ = _build_kisan_orchestrator(invocation, discover=discovery)
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _resend_turn()))

    (call,) = invocation.calls
    assert call.action is ProviderAction.INIT
    assert call.provider == _KISAN_PROVIDER
    assert call.sent()["enrolmentId"] == _KISAN_VALUES["enrolmentId"]
    assert discovery.asks_seen == []
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert isinstance(_stored_state(store).forms["offer-1"], FormAwaitingInit)


async def test_resend_with_nothing_waiting_calls_nobody() -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, _, turns = _build_kisan_orchestrator(invocation)

    finished = _finished(await _collect(orch, _resend_turn()))

    assert finished.outcome.cause is Cause.NO_INIT_PENDING
    assert invocation.calls == []
    assert turns.records["t1"].finished == finished


async def test_a_resend_the_provider_did_not_take_keeps_the_otp_form_open() -> None:
    orch, store, _ = _build_kisan_orchestrator(_init_failed(503))
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _resend_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert isinstance(_stored_state(store).forms["offer-1"], FormAwaitingInit)


async def test_a_button_the_waiting_form_never_offered_sends_nothing() -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(store, "offer-1", challenge_method=None)

    finished = _finished(await _collect(orch, _resend_turn()))

    assert finished.outcome.cause is Cause.NO_INIT_PENDING
    assert invocation.calls == []


async def test_a_farmer_resends_the_otp_then_files_with_it() -> None:
    invocation = RecordingActionInvocation(
        replies={ProviderAction.INIT: _CHALLENGE, ProviderAction.SUPPORT: _TICKET}
    )
    orch, store, _ = _build_kisan_orchestrator(invocation)

    offered = _finished(await _collect(orch, _kisan_details_turn())).content[-1]
    assert isinstance(offered, FormBlock) and offered.actions == (_RESEND,)
    await _collect(orch, _resend_turn(offered.form_id))
    await _collect(orch, _init_response_turn())

    assert [call.action for call in invocation.calls] == [
        ProviderAction.INIT,
        ProviderAction.INIT,
        ProviderAction.SUPPORT,
    ]
    assert invocation.calls[-1].sent()["challenge"]["response"]["value"] == _OTP
    assert _stored_state(store).forms["offer-1"].status is FormStatus.DONE


async def test_a_pressed_button_is_recorded_without_any_values() -> None:
    orch, store, turns = _build_kisan_orchestrator(
        RecordingActionInvocation(_CHALLENGE)
    )
    _save_waiting(store, "offer-1")

    await _collect(orch, _resend_turn())

    record = turns.records["t1"]
    assert record.turn is not None
    assert _KISAN_VALUES["enrolmentId"] not in record.turn.model_dump_json()


async def test_a_resend_names_the_button_and_the_press_on_the_turn_span(spans) -> None:
    orch, store, _ = _build_kisan_orchestrator(RecordingActionInvocation(_CHALLENGE))
    _save_waiting(store, "offer-1")

    await _collect(orch, _resend_turn())

    (turn_span,) = [
        span for span in spans.get_finished_spans() if span.name == "dss.turn"
    ]
    assert turn_span.attributes["form_action"] == "resend_init"
    assert turn_span.attributes["action_id"] == "press-1"
