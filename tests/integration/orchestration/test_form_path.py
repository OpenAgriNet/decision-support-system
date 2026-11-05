from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from pathlib import Path

import yaml

from dss.adapters.sinks.memory import MemoryTurnSink
from dss.config.clarification_text_loader import load_clarification_text
from dss.core.forms.models import (
    FieldErrorEntry,
    FieldValue,
    FormContext,
    FormErrorBlock,
    FormSubmission,
    ProviderAction,
)
from dss.core.forms.service import build_form_registry
from dss.core.intent.models import Intent, InteractionType, Scheme, SubjectCategory
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    FailureClass,
    ProviderCapability,
)
from dss.core.schema_packs.models import SchemaPackFiles
from dss.core.shared.models import (
    Cause,
    ClaimDelta,
    ConversationMessage,
    RefusalBlock,
    TextBlock,
    TurnContext,
    TurnEvent,
    TurnStatus,
    UserDetails,
    UserTurn,
)
from dss.orchestration.form_path import FormPath
from dss.orchestration.orchestrator import Components, Orchestrator
from dss.orchestration.redaction import RedactTexts, pass_through, redact_texts
from dss.ports.invocation import (
    SupportFailedError,
)
from tests.support.fakes import (
    FakeLocalizer,
    FakePromptProvider,
    FakeSchemeCatalog,
    RecordingActionInvocation,
)
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

_VALUES = {
    "enrolmentId": "PMFBY-2024-000123",
    "applicantMobile": "9876543210",
    "cropYear": "2024",
    "season": "Kharif",
    "grievance.description": "Claim for the Kharif crop loss has not been paid.",
}
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


async def _no_plan(*args, **kwargs):
    raise AssertionError("a form turn never runs the planner")


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
    values: Mapping[str, str], *, context: FormContext = _PMFBY_SUPPORT
) -> UserTurn:
    return UserTurn(
        input=FormSubmission(
            form_id="offer-1",
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
) -> tuple[Orchestrator, MemoryTurnSink]:
    pack = SchemaPackFiles(
        pack_name="Grievance",
        version="v0.1",
        profile_json='{"semantic_model": "provider-api"}',
        attributes_yaml=attributes_yaml,
        examples_json=(),
    )
    turns = MemoryTurnSink()
    components = Components(
        redact=redact,
        discover=discover
        or _FakeDiscovery(
            discovery or _providers_found(grievance_provider("pmfby-provider", "PMFBY"))
        ),
        plan=_no_plan,
        compose=compose or _FakeCompose("unused"),
        localize=localize or FakeLocalizer(),
    )
    path = FormPath(
        moderation_llm=moderation_llm or _FakeModerationLLM(),
        prompts=FakePromptProvider(),
        policies=policies or [],
        forms=build_form_registry([pack]),
        scheme_catalog=FakeSchemeCatalog(_PMFBY),
        components=components,
        invocation_for=lambda _turn: invocation or RecordingActionInvocation(),
        turns=turns,
        telemetry=telemetry or _StageOutcomes(),
        clarification_text=load_clarification_text(),
    )
    return Orchestrator(paths={FormSubmission: path}), turns


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
    kisan = FormContext(scheme="PM-KISAN", category="Grievance", action="support")
    orch, _ = _build_orchestrator_and_turn_sink()

    events = await _collect(orch, _form_turn(_VALUES, context=kisan))

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


async def test_a_failed_provider_call_is_marked_on_its_stage(spans) -> None:
    orch, _ = _build_orchestrator_and_turn_sink(invocation=_support_unavailable())

    await _collect(orch, _form_turn(_VALUES))

    finished = {span.name: span for span in spans.get_finished_spans()}
    assert finished["dss.stage.provider_action"].attributes["is_failure"] is True


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
