from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio
import pytest
import yaml

from dss.adapters.sinks.memory import MemoryTurnSink
from dss.config.clarification_text_loader import load_clarification_text
from dss.core.channel.service import answer_for_session_busy
from dss.core.forms.models import (
    ActionReply,
    FieldErrorEntry,
    FieldValue,
    FormBlock,
    FormContext,
    FormErrorBlock,
    FormStatus,
    FormSubmission,
    ProviderAction,
)
from dss.core.forms.service import WITHHELD, build_form_registry, with_init_awaited
from dss.core.intent.models import Intent, InteractionType, Scheme, SubjectCategory
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    FailureClass,
    ProviderCapability,
)
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.schema_packs.models import SchemaPackFiles
from dss.core.shared.models import (
    Cause,
    ClaimDelta,
    ConversationMessage,
    RefusalBlock,
    TextBlock,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnStatus,
    UserDetails,
    UserTurn,
)
from dss.core.shared.session_state import SessionState
from dss.core.shared.network import NetworkTransactionID
from dss.orchestration.form_path import FormPath, FormPathComponents
from dss.orchestration.orchestrator import Orchestrator
from dss.orchestration.redaction import RedactTexts, pass_through, redact_texts
from dss.orchestration.session import NoSessionStore, Sessions, SessionStore
from dss.ports.invocation import (
    InitFailedError,
    SupportFailedError,
)
from tests.support.fakes import (
    FakeLocalizer,
    FakePromptProvider,
    FakeSchemeCatalog,
    RecordingActionInvocation,
)
from tests.support.grievance_pack import build_grievance_forms
from tests.support.key_value import FakeKeyValueStore, UnreachableStore
from tests.support.providers import grievance_provider
from tests.support.turn_events import assert_well_formed

from .test_orchestrator import (
    DELETE_COMMAND,
    _FakeCompose,
    _FakeDiscovery,
    _FakeModerationLLM,
)
from .test_orchestrator_redaction import PHONE, POLICY, _FindsValues

_GRIEVANCE_YAML = (
    Path(__file__).parents[2] / "fixtures" / "api_packs" / "grievance_attributes.yaml"
).read_text(encoding="utf-8")

_PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
_PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PMFBY_STATUS = FormContext(scheme="PMFBY", category="Grievance", action="status")
_PMKISAN_INIT = FormContext(scheme="PM-KISAN", category="Grievance", action="init")
_PMKISAN_SUPPORT = FormContext(
    scheme="PM-KISAN", category="Grievance", action="support"
)
_KISAN_PROVIDER = grievance_provider("kisan-provider", "PM-KISAN")
_KISAN_VALUES = {
    "enrolmentId": "PMK1234567",
    "grievance.category.code": "G001",
    "grievance.description": "My last installment has not come.",
}
_CHALLENGE = {"challenge": {"issued": {"method": "OTP", "channel": "SMS"}}}

_VALUES = {
    "enrolmentId": "PMFBY-2024-000123",
    "applicantMobile": "9876543210",
    "cropYear": "2024",
    "season": "Kharif",
    "grievance.description": "Claim for the Kharif crop loss has not been paid.",
}
_OTP = "1234"
_INIT_RESPONSE = {"challenge.response.value": _OTP}
_LOOKUP = {"applicantMobile": "9876543210", "case.ticketNo": "100626000099001"}
_TICKET = {"case": {"ticketNo": "100626000099001", "filedOn": "2026-10-29"}}


def _providers_found(*providers: ProviderCapability) -> DiscoveryResult:
    return DiscoveryResult(
        answers={}, capabilities={0: providers}, failures={}, events=()
    )


def _support_unavailable(
    *, retry_after_seconds: int | None = None
) -> RecordingActionInvocation:
    return RecordingActionInvocation(
        failure=SupportFailedError(
            "openagrinet:Grievance",
            503,
            FailureClass.TRANSIENT,
            retry_after_seconds=retry_after_seconds,
        )
    )


def _support_refused() -> RecordingActionInvocation:
    return RecordingActionInvocation(
        failure=SupportFailedError("openagrinet:Grievance", 400, FailureClass.DEFECT)
    )


class _RecordingModerationLLM:
    def __init__(self) -> None:
        self.judged: list[str] = []

    async def structured(self, *, system_prompt, user_query, schema):
        self.judged.append(user_query)
        return schema(violated_policy_id=None)


class _BrokenModerationLLM:
    async def structured(self, *, system_prompt, user_query, schema):
        raise RuntimeError("model unavailable")


class _StageOutcomes:
    def __init__(self) -> None:
        self.noted: list[tuple[str, str]] = []

    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None:
        self.noted.append((name, outcome))


class _RecordingDiscovery:
    def __init__(self, result: DiscoveryResult) -> None:
        self._result = result
        self.intents: list[Intent] = []

    async def __call__(self, intent: Intent, turn: UserTurn, *, now) -> DiscoveryResult:
        self.intents.append(intent)
        return self._result


def _form_turn(
    values: Mapping[str, str],
    *,
    context: FormContext = _PMFBY_SUPPORT,
    form_id: str = "offer-1",
) -> UserTurn:
    return UserTurn(
        input=FormSubmission(
            form_id=form_id,
            context=context,
            submission_id="submission-1",
            values=tuple(
                FieldValue(name=name, value=value) for name, value in values.items()
            ),
        ),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


def _build_orchestrator_and_turn_sink(  # noqa: PLR0913
    *,
    localize: FakeLocalizer | None = None,
    redact: RedactTexts = pass_through,
    moderation_llm: _FakeModerationLLM | None = None,
    policies: list[Policy] | None = None,
    telemetry: _StageOutcomes | None = None,
    discovery: DiscoveryResult | None = None,
    discover: _RecordingDiscovery | None = None,
    attributes_yaml: str = _GRIEVANCE_YAML,
    invocation: RecordingActionInvocation | None = None,
    compose: _FakeCompose | None = None,
    sessions: Sessions | None = None,
) -> tuple[Orchestrator, MemoryTurnSink]:
    pack = SchemaPackFiles(
        pack_name="Grievance",
        version="v0.1",
        profile_json='{"semantic_model": "provider-api"}',
        attributes_yaml=attributes_yaml,
        examples_json=(),
    )
    turns = MemoryTurnSink()
    components = FormPathComponents(
        redact=redact,
        discover=discover
        or _FakeDiscovery(
            discovery or _providers_found(grievance_provider("pmfby-provider", "PMFBY"))
        ),
        compose=compose or _FakeCompose("unused"),
        localize=localize or FakeLocalizer(),
        invocation_for=lambda _turn: invocation or RecordingActionInvocation(),
    )
    path = FormPath(
        moderation_llm=moderation_llm or _FakeModerationLLM(),
        prompts=FakePromptProvider(),
        policies=policies or [],
        forms=build_form_registry([pack]),
        scheme_catalog=FakeSchemeCatalog(_PMFBY),
        components=components,
        turns=turns,
        telemetry=telemetry or _StageOutcomes(),
        clarification_text=load_clarification_text(),
    )
    orchestrator = Orchestrator(
        paths={FormSubmission: path},
        session_busy_answer=answer_for_session_busy(load_clarification_text()),
        sessions=sessions or NoSessionStore(),
    )
    return orchestrator, turns


async def _collect(orch: Orchestrator, turn: UserTurn) -> list[TurnEvent]:
    ctx = TurnContext(trace_id="t1", message_id="m1", session_id="s1", sender_id="c1")
    events = [event async for event in orch.run(turn, ctx)]
    assert_well_formed(events)
    return events


async def test_bad_values_hand_the_form_back_with_the_errors() -> None:
    orch, _ = _build_orchestrator_and_turn_sink()

    events = await _collect(orch, _form_turn({**_VALUES, "applicantMobile": "12345"}))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is Cause.INVALID_INPUT
    text, errors = finished.content
    assert isinstance(text, TextBlock)
    assert errors == FormErrorBlock(
        form_id="offer-1",
        context=_PMFBY_SUPPORT,
        submission_id="submission-1",
        fields=(
            FieldErrorEntry(
                name="applicantMobile",
                label="Applicant mobile",
                error="Applicant mobile is not in the right format.",
            ),
        ),
    )


async def test_the_record_holds_names_and_references_never_a_value() -> None:
    orch, turns = _build_orchestrator_and_turn_sink()

    await _collect(orch, _form_turn({**_VALUES, "applicantMobile": "12345"}))

    record = turns.records["t1"]
    assert record.turn is not None and record.finished is not None
    opened = record.turn.model_dump_json()
    closed = record.finished.model_dump_json()
    for value in (*_VALUES.values(), "12345"):
        assert value not in opened and value not in closed
    assert record.turn.input.submission_id == "submission-1"
    assert [field.name for field in record.turn.input.values] == list(_VALUES)


async def test_a_form_the_registry_does_not_have_is_unknown() -> None:
    unserved = FormContext(scheme="NO-SUCH", category="Grievance", action="support")
    orch, _ = _build_orchestrator_and_turn_sink()

    events = await _collect(orch, _form_turn(_VALUES, context=unserved))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is Cause.INVALID_INPUT
    (text,) = finished.content
    assert isinstance(text, TextBlock)
    assert text.text == load_clarification_text().unknown_form


async def test_a_registered_form_with_no_provider_action_is_unknown() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    schemas = attributes["components"]["schemas"]
    schemas["Grievance"]["x-oan-schema-by-action"]["advice"] = "GrievanceSupport"
    advice = FormContext(scheme="PMFBY", category="Grievance", action="advice")
    moderation_llm = _RecordingModerationLLM()
    orch, _ = _build_orchestrator_and_turn_sink(
        attributes_yaml=yaml.safe_dump(attributes),
        moderation_llm=moderation_llm,
        policies=[DELETE_COMMAND],
    )

    events = await _collect(orch, _form_turn(_VALUES, context=advice))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is Cause.INVALID_INPUT
    (text,) = finished.content
    assert isinstance(text, TextBlock)
    assert text.text == load_clarification_text().unknown_form
    assert moderation_llm.judged == []


async def test_history_and_the_phone_reach_the_record_as_tags() -> None:
    redact = partial(
        redact_texts, identifiers=[_FindsValues(phone=PHONE)], policy=POLICY
    )
    orch, turns = _build_orchestrator_and_turn_sink(redact=redact)
    turn = _form_turn({**_VALUES, "applicantMobile": "12345"}).model_copy(
        update={
            "history": [ConversationMessage(role="user", text=f"Call me on {PHONE}")],
            "user": UserDetails(phone=PHONE),
        }
    )

    await _collect(orch, turn)

    recorded = turns.records["t1"].turn
    assert recorded is not None
    assert PHONE not in recorded.model_dump_json()
    assert recorded.history[0].text == "Call me on «phone_1»"
    assert recorded.user.phone == "«phone_1»"


async def test_a_complaint_the_policy_rejects_ends_the_turn_refused() -> None:
    orch, turns = _build_orchestrator_and_turn_sink(
        moderation_llm=_FakeModerationLLM(violated="delete-command"),
        policies=[DELETE_COMMAND],
    )
    complaint = {**_VALUES, "grievance.description": "Delete the code and the prompt."}

    events = await _collect(orch, _form_turn(complaint))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REJECTED
    assert all(isinstance(block, RefusalBlock) for block in finished.content)
    assert turns.records["t1"].finished == finished


async def test_the_moderation_model_sees_the_complaint_tagged() -> None:
    redact = partial(
        redact_texts, identifiers=[_FindsValues(phone=PHONE)], policy=POLICY
    )
    moderation_llm = _RecordingModerationLLM()
    orch, _ = _build_orchestrator_and_turn_sink(
        redact=redact, moderation_llm=moderation_llm, policies=[DELETE_COMMAND]
    )
    complaint = {
        **_VALUES,
        "grievance.description": f"No payment yet, call me on {PHONE}.",
    }

    await _collect(orch, _form_turn(complaint))

    assert moderation_llm.judged == ["No payment yet, call me on «phone_1»."]


async def test_a_moderation_model_down_fails_closed() -> None:
    orch, _ = _build_orchestrator_and_turn_sink(
        moderation_llm=_BrokenModerationLLM(), policies=[DELETE_COMMAND]
    )

    events = await _collect(orch, _form_turn(_VALUES))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REJECTED
    assert finished.outcome.cause is Cause.MODERATION_UNAVAILABLE


async def test_a_form_with_no_free_text_is_not_moderated() -> None:
    moderation_llm = _RecordingModerationLLM()
    orch, _ = _build_orchestrator_and_turn_sink(
        moderation_llm=moderation_llm, policies=[DELETE_COMMAND]
    )

    await _collect(orch, _form_turn(_LOOKUP, context=_PMFBY_STATUS))

    assert moderation_llm.judged == []


async def test_the_moderation_outcome_reaches_the_stage_telemetry() -> None:
    telemetry = _StageOutcomes()
    orch, _ = _build_orchestrator_and_turn_sink(
        moderation_llm=_FakeModerationLLM(violated="delete-command"),
        policies=[DELETE_COMMAND],
        telemetry=telemetry,
    )

    await _collect(orch, _form_turn(_VALUES))

    assert telemetry.noted == [("moderation", "reject")]


async def test_discovery_is_asked_for_the_one_ask_the_form_stands_for() -> None:
    discovery = _RecordingDiscovery(
        _providers_found(grievance_provider("pmfby-provider", "PMFBY"))
    )
    orch, _ = _build_orchestrator_and_turn_sink(discover=discovery)

    await _collect(orch, _form_turn(_VALUES))

    (intent,) = discovery.intents
    (ask,) = intent.asks
    assert ask.subject_categories is SubjectCategory.GRIEVANCE
    assert ask.interaction_type is InteractionType.ACT
    assert ask.scheme == _PMFBY
    assert ask.place is None


async def test_no_provider_for_the_scheme_ends_the_turn_unserved() -> None:
    orch, turns = _build_orchestrator_and_turn_sink(
        discovery=_providers_found(grievance_provider("kisan-provider", "PM-KISAN"))
    )

    events = await _collect(orch, _form_turn(_VALUES))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.NO_MATCH
    (text,) = finished.content
    assert isinstance(text, TextBlock)
    assert text.text == load_clarification_text().no_provider
    assert turns.records["t1"].finished == finished


async def test_a_direct_answer_does_not_stand_in_for_a_provider() -> None:
    direct = DiscoveredAnswer(
        provider_id="leaflet",
        provider_name="Leaflet",
        capability="openagrinet:Grievance",
        resource_id="res:leaflet:how-to-file",
        attributes={},
        validity=None,
    )
    found = DiscoveryResult(
        answers={0: (direct,)}, capabilities={}, failures={}, events=()
    )
    orch, _ = _build_orchestrator_and_turn_sink(discovery=found)

    events = await _collect(orch, _form_turn(_VALUES))

    assert events[-1].outcome.status is TurnStatus.NO_MATCH


async def test_the_first_ranked_provider_for_the_forms_scheme_is_called() -> None:
    invocation = RecordingActionInvocation()
    found = _providers_found(
        grievance_provider("kisan-provider", "PM-KISAN"),
        grievance_provider("pmfby-provider", "PMFBY"),
        grievance_provider("pmfby-backup", "PMFBY"),
    )
    orch, _ = _build_orchestrator_and_turn_sink(discovery=found, invocation=invocation)

    await _collect(orch, _form_turn(_VALUES))

    (call,) = invocation.calls
    assert call.action is ProviderAction.SUPPORT
    assert call.provider.provider_id == "pmfby-provider"
    assert call.attributes["enrolmentId"] == _VALUES["enrolmentId"]


async def test_a_status_form_is_sent_with_status() -> None:
    invocation = RecordingActionInvocation()
    orch, _ = _build_orchestrator_and_turn_sink(invocation=invocation)

    await _collect(orch, _form_turn(_LOOKUP, context=_PMFBY_STATUS))

    (call,) = invocation.calls
    assert call.action is ProviderAction.STATUS


async def test_the_provider_call_is_its_own_stage(spans) -> None:
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(_TICKET)
    )

    await _collect(orch, _form_turn(_VALUES))

    finished = {span.name: span for span in spans.get_finished_spans()}
    assert "dss.stage.planner" not in finished
    attributes = finished["dss.stage.provider_action"].attributes
    assert attributes["action"] == "support"
    assert attributes["provider_id"] == "pmfby-provider"
    assert attributes["is_failure"] is False


async def test_a_stream_closed_mid_answer_closes_the_composer() -> None:
    compose = _FakeCompose("Filed. Ticket 1.", chunks=("Filed. ", "Ticket 1."))
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(_TICKET), compose=compose
    )
    ctx = TurnContext(trace_id="t1", message_id="m1", session_id="s1", sender_id="c1")

    events = orch.run(_form_turn(_VALUES), ctx)
    async for event in events:
        if isinstance(event, ClaimDelta):
            break
    assert not compose.closed
    await events.aclose()  # type: ignore[attr-defined]

    assert compose.closed


async def test_a_failed_provider_call_is_marked_on_its_stage(spans) -> None:
    orch, _ = _build_orchestrator_and_turn_sink(invocation=_support_unavailable())

    await _collect(orch, _form_turn(_VALUES))

    finished = {span.name: span for span in spans.get_finished_spans()}
    assert finished["dss.stage.provider_action"].attributes["is_failure"] is True


async def test_our_credentials_refused_on_a_filing_are_logged_and_read_as_rejected(
    caplog,
) -> None:
    caplog.set_level(logging.WARNING)
    refused = SupportFailedError("openagrinet:Grievance", 403, FailureClass.DEFECT)
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(failure=refused)
    )

    finished = _finished(await _collect(orch, _form_turn(_VALUES)))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.PROVIDER_REJECTED
    assert "request_id=t1 provider_auth_refused status_code=403" in caplog.text


async def test_the_provider_stage_shows_the_call_and_the_reply_without_values(
    spans, monkeypatch
) -> None:
    monkeypatch.setenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", "true")
    echoed = {**_TICKET, "applicantMobile": _VALUES["applicantMobile"]}
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(echoed)
    )

    await _collect(orch, _form_turn(_VALUES))

    (stage,) = [
        span
        for span in spans.get_finished_spans()
        if span.name == "dss.stage.provider_action"
    ]
    assert stage.attributes["langfuse.observation.input"] == "support to PMFBY-PROVIDER"
    output = stage.attributes["langfuse.observation.output"]
    assert "100626000099001" in output
    assert _VALUES["applicantMobile"] not in output


async def test_a_failed_provider_call_shows_its_reason_on_the_stage(
    spans, monkeypatch
) -> None:
    monkeypatch.setenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", "true")
    orch, _ = _build_orchestrator_and_turn_sink(invocation=_support_unavailable())

    await _collect(orch, _form_turn(_VALUES))

    (stage,) = [
        span
        for span in spans.get_finished_spans()
        if span.name == "dss.stage.provider_action"
    ]
    output = stage.attributes["langfuse.observation.output"]
    assert output == "support failed with status 503"


async def test_a_filed_form_is_answered_with_the_composers_text() -> None:
    compose = _FakeCompose("Your complaint is filed. Ticket 100626000099001.")
    orch, turns = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(_TICKET), compose=compose
    )

    events = await _collect(orch, _form_turn(_VALUES))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.ANSWERED
    assert finished.outcome.cause is None
    (text,) = finished.content
    assert isinstance(text, TextBlock)
    assert text.text == "Your complaint is filed. Ticket 100626000099001."
    assert any(isinstance(event, ClaimDelta) for event in events)
    assert turns.records["t1"].finished == finished


async def test_the_composer_gets_the_providers_reply_as_evidence() -> None:
    compose = _FakeCompose("Your complaint is filed.")
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(_TICKET), compose=compose
    )

    await _collect(orch, _form_turn(_VALUES))

    (source,) = compose.evidence.sources
    assert source.name == "PMFBY-PROVIDER"
    (ask,) = compose.evidence.ask_evidences
    (result,) = ask.results
    assert result.data == _TICKET


async def test_the_composer_sees_the_tagged_history_and_no_form_value() -> None:
    redact = partial(
        redact_texts, identifiers=[_FindsValues(phone=PHONE)], policy=POLICY
    )
    compose = _FakeCompose("Your complaint is filed. Ticket 100626000099001.")
    orch, _ = _build_orchestrator_and_turn_sink(
        redact=redact, invocation=RecordingActionInvocation(_TICKET), compose=compose
    )
    turn = _form_turn({**_VALUES, "applicantMobile": "9123456789"}).model_copy(
        update={
            "history": [ConversationMessage(role="user", text=f"Call me on {PHONE}")]
        }
    )

    await _collect(orch, turn)

    assert compose.turn.history[0].text == "Call me on «phone_1»"
    assert "9123456789" not in compose.turn.model_dump_json()


async def test_a_support_call_the_provider_could_not_take_keeps_the_form() -> None:
    compose = _FakeCompose("must not be written")
    orch, turns = _build_orchestrator_and_turn_sink(
        invocation=_support_unavailable(retry_after_seconds=120), compose=compose
    )

    events = await _collect(orch, _form_turn(_VALUES))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.PROVIDER_UNAVAILABLE
    assert finished.outcome.retry_after_seconds == 120
    _text, kept = finished.content
    assert kept == FormErrorBlock(
        form_id="offer-1", context=_PMFBY_SUPPORT, submission_id="submission-1"
    )
    assert compose.calls == 0
    assert turns.records["t1"].finished == finished


async def test_a_support_call_the_provider_refused_is_not_retryable() -> None:
    orch, _ = _build_orchestrator_and_turn_sink(invocation=_support_refused())

    events = await _collect(orch, _form_turn(_VALUES))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.PROVIDER_REJECTED
    assert finished.outcome.retry_after_seconds is None
    _text, kept = finished.content
    assert isinstance(kept, FormErrorBlock)
    assert kept.fields == ()


def _finished(events: list[TurnEvent]) -> TurnFinished:
    finished = events[-1]
    assert isinstance(finished, TurnFinished)
    return finished


def _with_a_store() -> tuple[FakeKeyValueStore, SessionStore]:
    store = FakeKeyValueStore()
    return store, SessionStore(store)


def _stored_state(store: FakeKeyValueStore) -> SessionState:
    stored = store.values.get("c1:s1")
    return (
        SessionState() if stored is None else SessionState.model_validate_json(stored)
    )


async def test_a_filed_form_is_kept_done_without_its_values() -> None:
    store, sessions = _with_a_store()
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=RecordingActionInvocation(_TICKET), sessions=sessions
    )

    await _collect(orch, _form_turn(_VALUES))

    entry = _stored_state(store).forms["offer-1"]
    assert entry.status is FormStatus.DONE
    assert entry.definition.context == _PMFBY_SUPPORT
    assert {value.value for value in entry.submission.values} == {WITHHELD}


async def test_a_form_sent_again_after_filing_is_already_filed() -> None:
    invocation = RecordingActionInvocation(_TICKET)
    _, sessions = _with_a_store()
    orch, turns = _build_orchestrator_and_turn_sink(
        invocation=invocation, sessions=sessions
    )
    await _collect(orch, _form_turn(_VALUES))

    events = await _collect(orch, _form_turn(_VALUES))

    finished = _finished(events)
    assert finished.outcome.status is TurnStatus.REJECTED
    assert finished.outcome.cause is Cause.ALREADY_FILED
    (text,) = finished.content
    assert text == TextBlock(text=load_clarification_text().already_filed)
    assert len(invocation.calls) == 1
    assert turns.records["t1"].finished == finished


async def test_a_form_the_provider_did_not_take_can_be_sent_again() -> None:
    store, sessions = _with_a_store()
    invocation = _support_unavailable()
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=invocation, sessions=sessions
    )
    await _collect(orch, _form_turn(_VALUES))

    await _collect(orch, _form_turn(_VALUES))

    assert len(invocation.calls) == 2
    assert _stored_state(store).forms == {}


async def test_an_init_response_with_nothing_waiting_calls_nobody() -> None:
    invocation = RecordingActionInvocation()
    discovery = _RecordingDiscovery(_providers_found())
    orch, turns = _build_orchestrator_and_turn_sink(
        invocation=invocation,
        discover=discovery,
        sessions=_with_a_store()[1],
    )

    events = await _collect(orch, _form_turn(_INIT_RESPONSE, context=_PMKISAN_INIT))

    finished = _finished(events)
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is Cause.NO_INIT_PENDING
    (text,) = finished.content
    assert text == TextBlock(text=load_clarification_text().no_init_pending)
    assert invocation.calls == []
    assert discovery.intents == []
    record = turns.records["t1"]
    assert record.turn is not None and record.finished == finished
    assert _OTP not in record.turn.model_dump_json()


def _init_failed(status_code: int) -> RecordingActionInvocation:
    failure_class = FailureClass.DEFECT if status_code < 500 else FailureClass.TRANSIENT
    return RecordingActionInvocation(
        failure=InitFailedError("openagrinet:Grievance", status_code, failure_class)
    )


def _build_kisan_orchestrator(
    invocation: RecordingActionInvocation, **overrides
) -> tuple[Orchestrator, FakeKeyValueStore, MemoryTurnSink]:
    store, sessions = _with_a_store()
    orch, turns = _build_orchestrator_and_turn_sink(
        invocation=invocation,
        discovery=_providers_found(_KISAN_PROVIDER),
        sessions=sessions,
        **overrides,
    )
    return orch, store, turns


def _save_waiting(
    store: FakeKeyValueStore,
    form_id: str,
    *,
    context: FormContext = _PMKISAN_SUPPORT,
    values: Mapping[str, str] = _KISAN_VALUES,
) -> None:
    definition = build_grievance_forms().find(context)
    assert definition is not None
    details = _form_turn(values, context=context, form_id=form_id).input
    assert isinstance(details, FormSubmission)
    waiting = with_init_awaited(
        _stored_state(store).forms,
        form_id,
        definition,
        details,
        provider=_KISAN_PROVIDER,
        now=datetime(2026, 11, 10, 9, 30, tzinfo=UTC),
    )
    store.values["c1:s1"] = SessionState(forms=waiting).model_dump_json()


def _kisan_details_turn(form_id: str = "offer-1") -> UserTurn:
    return _form_turn(_KISAN_VALUES, context=_PMKISAN_SUPPORT, form_id=form_id)


async def test_a_form_needing_init_sends_init_once_with_only_its_paths() -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, _, _ = _build_kisan_orchestrator(invocation)

    await _collect(orch, _kisan_details_turn())

    (call,) = invocation.calls
    assert call.action is ProviderAction.INIT
    assert call.provider == _KISAN_PROVIDER
    assert call.attributes["enrolmentId"] == _KISAN_VALUES["enrolmentId"]
    assert call.attributes["scheme"] == {"code": "PM-KISAN"}
    assert "grievance" not in call.attributes
    assert "challenge" not in call.attributes


async def test_after_init_the_farmer_gets_the_init_form() -> None:
    orch, _, turns = _build_kisan_orchestrator(RecordingActionInvocation(_CHALLENGE))

    finished = _finished(await _collect(orch, _kisan_details_turn()))

    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is None
    told, offered = finished.content
    assert told == TextBlock(text=load_clarification_text().init_response_needed)
    assert isinstance(offered, FormBlock)
    assert offered.form.context == _PMKISAN_INIT
    assert [field.name for field in offered.form.fields] == ["challenge.response.value"]
    assert turns.records["t1"].finished == finished


async def test_after_init_the_details_form_waits_with_its_provider() -> None:
    orch, store, _ = _build_kisan_orchestrator(RecordingActionInvocation(_CHALLENGE))

    await _collect(orch, _kisan_details_turn())

    entry = _stored_state(store).forms["offer-1"]
    assert entry.status is FormStatus.AWAITING_INIT
    assert entry.provider == _KISAN_PROVIDER
    assert entry.definition.context == _PMKISAN_SUPPORT


@pytest.mark.parametrize(
    "sessions",
    [NoSessionStore(), SessionStore(UnreachableStore())],
    ids=["no-store", "store-unreachable"],
)
async def test_a_form_needing_init_without_a_kept_session_sends_nothing(
    sessions: Sessions,
) -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=invocation,
        discovery=_providers_found(_KISAN_PROVIDER),
        sessions=sessions,
    )

    finished = _finished(await _collect(orch, _kisan_details_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.INTERNAL
    assert finished.content == (
        TextBlock(text=load_clarification_text().form_cannot_complete),
    )
    assert invocation.calls == []


async def test_a_refused_complaint_never_sends_init_and_keeps_the_open_flow() -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, store, _ = _build_kisan_orchestrator(
        invocation,
        moderation_llm=_FakeModerationLLM(violated="delete-command"),
        policies=[DELETE_COMMAND],
    )
    _save_waiting(store, "offer-1")

    events = await _collect(orch, _kisan_details_turn("offer-2"))

    assert _finished(events).outcome.status is TurnStatus.REJECTED
    assert invocation.calls == []
    assert set(_stored_state(store).forms) == {"offer-1"}


async def test_no_provider_for_the_scheme_never_sends_init() -> None:
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, _ = _build_orchestrator_and_turn_sink(
        invocation=invocation, sessions=_with_a_store()[1]
    )

    events = await _collect(orch, _kisan_details_turn())

    assert _finished(events).outcome.status is TurnStatus.NO_MATCH
    assert invocation.calls == []


@pytest.mark.parametrize(
    ("invocation", "cause", "text_key"),
    [
        (_init_failed(404), Cause.PROVIDER_REJECTED, "init_rejected"),
        (RecordingActionInvocation({}), Cause.PROVIDER_REJECTED, "init_rejected"),
    ],
    ids=["E9-refused", "E10-no-challenge"],
)
async def test_an_init_that_fails_hands_the_details_form_back(
    invocation: RecordingActionInvocation, cause: Cause, text_key: str
) -> None:
    orch, store, _ = _build_kisan_orchestrator(invocation)

    finished = _finished(await _collect(orch, _kisan_details_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is cause
    told, kept = finished.content
    assert told == TextBlock(text=getattr(load_clarification_text(), text_key))
    assert kept == FormErrorBlock(
        form_id="offer-1", context=_PMKISAN_SUPPORT, submission_id="submission-1"
    )
    assert [call.action for call in invocation.calls] == [ProviderAction.INIT]
    assert _stored_state(store).forms == {}


async def test_an_init_the_pack_does_not_give_is_our_fault_and_sends_nothing() -> None:
    attributes = yaml.safe_load(_GRIEVANCE_YAML)
    del attributes["components"]["schemas"]["Grievance"]["x-oan-schema-by-action"][
        "init"
    ]
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, store, _ = _build_kisan_orchestrator(
        invocation, attributes_yaml=yaml.safe_dump(attributes)
    )

    finished = _finished(await _collect(orch, _kisan_details_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.INTERNAL
    assert finished.content == (TextBlock(text=load_clarification_text().went_wrong),)
    assert invocation.calls == []
    assert _stored_state(store).forms == {}


async def test_an_init_refusing_our_credentials_is_our_fault(caplog) -> None:
    caplog.set_level(logging.WARNING)
    orch, store, _ = _build_kisan_orchestrator(_init_failed(401))

    finished = _finished(await _collect(orch, _kisan_details_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.INTERNAL
    assert finished.content == (TextBlock(text=load_clarification_text().went_wrong),)
    assert _stored_state(store).forms == {}
    assert "request_id=t1 provider_auth_refused status_code=401" in caplog.text


async def test_the_same_details_form_sent_again_while_waiting_sends_a_new_init() -> (
    None
):
    invocation = RecordingActionInvocation(_CHALLENGE)
    orch, store, _ = _build_kisan_orchestrator(invocation)
    first = _finished(await _collect(orch, _kisan_details_turn()))

    second = _finished(await _collect(orch, _kisan_details_turn()))

    assert [call.action for call in invocation.calls] == [ProviderAction.INIT] * 2
    first_offer, second_offer = first.content[1], second.content[1]
    assert isinstance(first_offer, FormBlock) and isinstance(second_offer, FormBlock)
    assert first_offer.form_id != second_offer.form_id
    assert _stored_state(store).forms["offer-1"].status is FormStatus.AWAITING_INIT
    assert store.set_calls == ["c1:s1", "c1:s1"]


async def test_a_new_details_form_replaces_the_open_flow() -> None:
    orch, store, _ = _build_kisan_orchestrator(RecordingActionInvocation(_CHALLENGE))
    await _collect(orch, _kisan_details_turn("offer-1"))

    await _collect(orch, _kisan_details_turn("offer-2"))

    assert set(_stored_state(store).forms) == {"offer-2"}


def _init_response_turn(
    values: Mapping[str, str] = _INIT_RESPONSE, *, context: FormContext = _PMKISAN_INIT
) -> UserTurn:
    return _form_turn(values, context=context, form_id="otp-1")


def _support_fails(status_code: int, **kwargs) -> RecordingActionInvocation:
    failure_class = FailureClass.DEFECT if status_code < 500 else FailureClass.TRANSIENT
    return RecordingActionInvocation(
        failures={
            ProviderAction.SUPPORT: SupportFailedError(
                "openagrinet:Grievance", status_code, failure_class, **kwargs
            )
        }
    )


async def test_an_init_response_files_the_waiting_form_with_its_provider() -> None:
    invocation = RecordingActionInvocation(_TICKET)
    discovery = _RecordingDiscovery(_providers_found())
    orch, store, _ = _build_kisan_orchestrator(
        invocation,
        discover=discovery,
        # Turn 3 is not moderated: this would refuse it.
        moderation_llm=_FakeModerationLLM(violated="delete-command"),
        policies=[DELETE_COMMAND],
    )
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.status is TurnStatus.ANSWERED
    (call,) = invocation.calls
    assert call.action is ProviderAction.SUPPORT
    assert call.provider == _KISAN_PROVIDER
    assert call.attributes["enrolmentId"] == _KISAN_VALUES["enrolmentId"]
    assert call.attributes["challenge"] == {
        "response": {"method": "OTP", "value": _OTP}
    }
    assert discovery.intents == []
    assert _stored_state(store).forms["offer-1"].status is FormStatus.DONE


async def test_a_pm_kisan_grievance_is_filed_over_two_form_turns() -> None:
    invocation = RecordingActionInvocation(
        replies={ProviderAction.INIT: _CHALLENGE, ProviderAction.SUPPORT: _TICKET}
    )
    compose = _FakeCompose("Your complaint is filed.")
    orch, store, _ = _build_kisan_orchestrator(invocation, compose=compose)

    offered = _finished(await _collect(orch, _kisan_details_turn()))
    filed = _finished(await _collect(orch, _init_response_turn()))

    assert offered.outcome.status is TurnStatus.REQUIRES_INPUT
    assert filed.outcome.status is TurnStatus.ANSWERED
    assert [call.action for call in invocation.calls] == [
        ProviderAction.INIT,
        ProviderAction.SUPPORT,
    ]
    assert _stored_state(store).forms["offer-1"].status is FormStatus.DONE


async def test_an_init_response_for_another_scheme_finds_nothing_waiting() -> None:
    invocation = RecordingActionInvocation(_TICKET)
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(store, "offer-1")
    pmfby_init = _PMKISAN_INIT.model_copy(update={"scheme": "PMFBY"})

    finished = _finished(await _collect(orch, _init_response_turn(context=pmfby_init)))

    assert finished.outcome.cause is Cause.NO_INIT_PENDING
    assert invocation.calls == []
    assert _stored_state(store).forms["offer-1"].status is FormStatus.AWAITING_INIT


async def test_a_used_init_response_finds_nothing_waiting() -> None:
    invocation = RecordingActionInvocation(_TICKET)
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(store, "offer-1")
    await _collect(orch, _init_response_turn())

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.cause is Cause.NO_INIT_PENDING
    assert len(invocation.calls) == 1


async def test_saved_details_no_longer_valid_end_the_flow() -> None:
    invocation = RecordingActionInvocation(_TICKET)
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(
        store, "offer-1", values={**_KISAN_VALUES, "enrolmentId": "not valid!"}
    )

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.INTERNAL
    assert finished.content == (
        TextBlock(text=load_clarification_text().saved_details_unusable),
    )
    assert invocation.calls == []
    assert _stored_state(store).forms == {}


@pytest.mark.parametrize(
    "response",
    [{"challenge.response.value": "12ab"}, {**_INIT_RESPONSE, "enrolmentId": "x"}],
    ids=["bad-pattern", "another-field"],
)
async def test_a_bad_init_response_hands_the_init_form_back(
    response: dict[str, str],
) -> None:
    invocation = RecordingActionInvocation(_TICKET)
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _init_response_turn(response)))

    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is Cause.INVALID_INPUT
    _text, errors = finished.content
    assert isinstance(errors, FormErrorBlock)
    assert errors.form_id == "otp-1" and errors.context == _PMKISAN_INIT
    assert invocation.calls == []
    assert _stored_state(store).forms["offer-1"].status is FormStatus.AWAITING_INIT


def _assert_init_form_handed_back(finished: TurnFinished, told: str) -> None:
    text, errors = finished.content
    assert text == TextBlock(text=told)
    assert isinstance(errors, FormErrorBlock)
    assert errors.form_id == "otp-1" and errors.context == _PMKISAN_INIT
    assert [field.name for field in errors.fields] == ["challenge.response.value"]


async def test_a_refused_init_response_hands_the_init_form_back_and_waits() -> None:
    orch, store, _ = _build_kisan_orchestrator(_support_fails(400))
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert finished.outcome.cause is Cause.INIT_RESPONSE_REFUSED
    _assert_init_form_handed_back(
        finished, load_clarification_text().init_response_refused
    )
    entry = _stored_state(store).forms["offer-1"]
    assert entry.status is FormStatus.AWAITING_INIT
    assert entry.provider == _KISAN_PROVIDER
    assert store.set_calls == ["c1:s1"]


async def test_an_init_response_never_taken_in_is_handed_back_to_send_again() -> None:
    orch, store, _ = _build_kisan_orchestrator(
        _support_fails(429, retry_after_seconds=30)
    )
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.status is TurnStatus.UNAVAILABLE
    assert finished.outcome.cause is Cause.PROVIDER_UNAVAILABLE
    assert finished.outcome.retry_after_seconds == 30
    _assert_init_form_handed_back(
        finished, load_clarification_text().init_response_not_sent
    )
    assert _stored_state(store).forms["offer-1"].status is FormStatus.AWAITING_INIT
    assert store.set_calls == ["c1:s1"]


async def test_an_init_response_that_may_have_gone_through_ends_the_flow() -> None:
    orch, store, _ = _build_kisan_orchestrator(_support_fails(503))
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.cause is Cause.PROVIDER_OUTCOME_UNKNOWN
    assert _stored_state(store).forms == {}


async def test_our_credentials_refused_with_an_init_response_are_logged(
    caplog,
) -> None:
    caplog.set_level(logging.WARNING)
    orch, store, _ = _build_kisan_orchestrator(_support_fails(401))
    _save_waiting(store, "offer-1")

    finished = _finished(await _collect(orch, _init_response_turn()))

    assert finished.outcome.cause is Cause.INTERNAL
    assert _stored_state(store).forms == {}
    assert "request_id=t1 provider_auth_refused status_code=401" in caplog.text


class _SupportInFlight(RecordingActionInvocation):
    def __init__(self) -> None:
        super().__init__()
        self.in_flight = anyio.Event()

    async def support(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        self.in_flight.set()
        await anyio.sleep_forever()
        raise AssertionError("unreachable")


async def test_a_stream_closed_while_the_response_is_in_flight_ends_the_flow() -> None:
    invocation = _SupportInFlight()
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(store, "offer-1")
    ctx = TurnContext(trace_id="t1", message_id="m1", session_id="s1", sender_id="c1")

    async def consume() -> None:
        async for _event in orch.run(_init_response_turn(), ctx):
            pass

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        await invocation.in_flight.wait()
        tasks.cancel_scope.cancel()

    assert _stored_state(store).forms == {}
    assert not store.is_locked("c1:s1")


async def test_a_stream_closed_after_the_answer_starts_leaves_the_form_done() -> None:
    orch, store, _ = _build_kisan_orchestrator(
        RecordingActionInvocation(_TICKET), compose=_FakeCompose("Filed. Ticket 1.")
    )
    _save_waiting(store, "offer-1")
    ctx = TurnContext(trace_id="t1", message_id="m1", session_id="s1", sender_id="c1")

    events = orch.run(_init_response_turn(), ctx)
    async for event in events:
        if isinstance(event, ClaimDelta):
            break
    await events.aclose()  # type: ignore[attr-defined]

    assert _stored_state(store).forms["offer-1"].status is FormStatus.DONE
    assert not store.is_locked("c1:s1")


async def test_a_crash_in_the_provider_call_leaves_the_flow_removed() -> None:
    invocation = RecordingActionInvocation(
        failures={ProviderAction.SUPPORT: RuntimeError("connection reset")}
    )
    orch, store, _ = _build_kisan_orchestrator(invocation)
    _save_waiting(store, "offer-1")

    with pytest.raises(RuntimeError):
        await _collect(orch, _init_response_turn())

    assert _stored_state(store).forms == {}


async def test_a_crash_in_redaction_on_an_init_response_keeps_the_flow_open() -> None:
    async def broken_redact(texts, *, request_id):
        raise RuntimeError("redaction down")

    orch, store, _ = _build_kisan_orchestrator(
        RecordingActionInvocation(_TICKET), redact=broken_redact
    )
    _save_waiting(store, "offer-1")

    with pytest.raises(RuntimeError):
        await _collect(orch, _init_response_turn())

    assert _stored_state(store).forms["offer-1"].status is FormStatus.AWAITING_INIT


async def test_the_init_response_never_reaches_state_record_trace_or_logs(
    spans, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("DSS_TRACE_INCLUDE_MESSAGE_CONTENT", "true")
    caplog.set_level(logging.DEBUG)
    otp = "4821"
    # The provider echoes what it was sent, the OTP too.
    echoed = {**_TICKET, "challenge": {"response": {"method": "OTP", "value": otp}}}
    invocation = RecordingActionInvocation(
        replies={ProviderAction.INIT: _CHALLENGE, ProviderAction.SUPPORT: echoed}
    )
    compose = _FakeCompose("Your complaint is filed.")
    orch, store, turns = _build_kisan_orchestrator(invocation, compose=compose)
    await _collect(orch, _kisan_details_turn())

    await _collect(orch, _init_response_turn({"challenge.response.value": otp}))

    assert invocation.calls[-1].attributes["challenge"]["response"]["value"] == otp
    assert otp not in store.values["c1:s1"]
    record = turns.records["t1"]
    assert record.turn is not None and record.finished is not None
    assert otp not in record.turn.model_dump_json()
    assert otp not in record.finished.model_dump_json()
    assert otp not in repr(compose.evidence)
    traced = [str(dict(span.attributes or {})) for span in spans.get_finished_spans()]
    assert any("100626000099001" in attributes for attributes in traced)
    assert all(otp not in attributes for attributes in traced)
    assert otp not in caplog.text


async def test_on_an_init_response_history_and_phone_reach_the_record_as_tags() -> None:
    redact = partial(
        redact_texts, identifiers=[_FindsValues(phone=PHONE)], policy=POLICY
    )
    orch, store, turns = _build_kisan_orchestrator(
        RecordingActionInvocation(_TICKET),
        redact=redact,
        compose=_FakeCompose("Filed."),
    )
    _save_waiting(store, "offer-1")
    turn = _init_response_turn().model_copy(
        update={
            "history": [ConversationMessage(role="user", text=f"Call me on {PHONE}")],
            "user": UserDetails(phone=PHONE),
        }
    )

    await _collect(orch, turn)

    recorded = turns.records["t1"].turn
    assert recorded is not None
    assert PHONE not in recorded.model_dump_json()
    assert recorded.history[0].text == "Call me on «phone_1»"
    assert recorded.user.phone == "«phone_1»"
