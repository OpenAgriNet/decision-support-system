"""Tier 3 — the status tool wired into a real Pydantic AI Agent.

FunctionModel scripts the one decision the model makes, which provider; the
loop runs it against real tool-calling machinery with the network
(CapabilityInvocation) faked.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.forms.models import (
    ActionField,
    ActionReply,
    ActionSchema,
    FieldSource,
    Filing,
    FormContext,
    ValidSubmission,
)
from dss.core.intent.models import Ask, Intent, InteractionType, Scheme, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Skill, Verdict
from dss.core.provider_discovery.models import (
    DiscoveryResult,
    FailureClass,
    ProviderCapability,
)
from dss.core.shared.models import TextQuery, UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent
from dss.ports.invocation import StatusFailedError

PROVIDER = ProviderCapability(
    provider_id="pmfby",
    provider_name="PMFBY Grievance Portal",
    capability="openagrinet:Grievance",
    resource_id="res:pmfby:grievance",
    observed_categories=("Grievance",),
)


def _action(action: str) -> ActionSchema:
    return ActionSchema(
        context=FormContext(scheme="PMFBY", category="Grievance", action=action),
        must_send=(
            ActionField(
                path="informationMode",
                source=FieldSource.FIXED,
                allowed_values=("OnDemand",),
            ),
            ActionField(
                path="scheme.code", source=FieldSource.FIXED, allowed_values=("PMFBY",)
            ),
            ActionField(
                path="applicantMobile", source=FieldSource.FARMER, pii_class="contact"
            ),
            ActionField(path="case.ticketNo", source=FieldSource.FARMER),
        ),
        jsonld={"@type": "openagrinet:Grievance"},
    )


STATUS = _action("status")

PHONE = "9876543210"
TICKET = "100626000099001"
VALUES = {"applicantMobile": PHONE, "case.ticketNo": TICKET}

READ = {
    "@type": "openagrinet:Grievance",
    "scheme": {"code": "PMFBY"},
    "applicantMobile": PHONE,
    "case": {
        "ticketNo": TICKET,
        "status": {"code": "UnderReview"},
        "filedOn": "2026-09-28",
        "cropName": "Paddy",
    },
}


class _FakeInvocation:
    def __init__(self) -> None:
        self.read: list[dict] = []

    async def status(self, capability, attributes, transaction_id, *, visibility=None):
        self.read.append(attributes)
        return ActionReply(attributes=READ)

    async def support(self, *args, **kwargs):
        raise AssertionError("support must not be called for a status check")

    async def select(self, *args, **kwargs):
        raise AssertionError("select must not be called for a status check")


def _skill() -> Skill:
    return Skill(
        id="provider-action",
        domain="agriculture",
        description="Send a form to a provider.",
        guidance="call status once",
        tool_names=("describe_capability", "support", "status"),
    )


def _deps(invocation: _FakeInvocation, action: ActionSchema = STATUS) -> PlannerDeps:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    ask = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.OBSERVE,
        scheme=Scheme(code="PMFBY", name="PMFBY"),
    )
    return PlannerDeps(
        turn=UserTurn(
            input=TextQuery(text="unused"),
            transaction_id="txn-1",
            session_id="s-1",
            source_lang="en",
            target_lang="en",
            channel="web",
        ),
        intent=Intent(asks=(ask,), confidence=1.0),
        discovery=DiscoveryResult(
            answers={}, capabilities={0: (PROVIDER,)}, failures={}, events=()
        ),
        schemas={},
        schema_context_index={},
        invocation=invocation,
        verdict=verdict,
        filing=Filing(
            submission=ValidSubmission(
                definition=action.form(), submission_id="submission-1", values=VALUES
            ),
            action=action,
        ),
    )


def _calls_then_answers(tool_name: str):
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=tool_name,
                        args={"ask_index": 0, "resource_id": PROVIDER.resource_id},
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    return respond


async def test_status_reads_the_case_with_the_chosen_provider() -> None:
    invocation = _FakeInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_calls_then_answers("status"))):
        await agent.run("check it", deps=deps)

    (body,) = invocation.read
    assert body["applicantMobile"] == PHONE
    assert body["case"] == {"ticketNo": TICKET}
    assert body["informationMode"] == "OnDemand"
    [(ask_index, answer)] = deps.raw_answers
    assert ask_index == 0
    assert answer.attributes["case"]["status"] == {"code": "UnderReview"}
    assert deps.failures == []


async def test_the_values_the_farmer_typed_do_not_reach_the_answer() -> None:
    """The desk echoes the phone and the ticket; neither may reach the
    composer, which writes the reply."""

    invocation = _FakeInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_calls_then_answers("status"))):
        await agent.run("check it", deps=deps)

    [(_, answer)] = deps.raw_answers
    assert "applicantMobile" not in answer.attributes
    assert "ticketNo" not in answer.attributes["case"]
    assert answer.attributes["case"]["cropName"] == "Paddy"


class _RefusingInvocation(_FakeInvocation):
    async def status(self, capability, attributes, transaction_id, *, visibility=None):
        self.read.append(attributes)
        raise StatusFailedError(capability.capability, 400, FailureClass.DEFECT)


async def test_a_refused_read_is_a_failure_that_is_not_retryable() -> None:
    """How a desk says it knows no such case; the form path tells the farmer
    no complaint was found."""

    invocation = _RefusingInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_calls_then_answers("status"))):
        await agent.run("check it", deps=deps)

    assert deps.raw_answers == []
    (failure,) = deps.failures
    assert failure.ask_index == 0
    assert failure.capability == "openagrinet:Grievance"
    assert failure.retryable is False
    assert failure.reason == "status failed with status 400"


async def test_a_status_form_is_not_filed_through_support() -> None:
    """The tool must match the form's action: a status form sent through
    `support` would file a blank grievance."""

    invocation = _FakeInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_calls_then_answers("support"))):
        await agent.run("check it", deps=deps)

    assert invocation.read == []
    assert deps.raw_answers == []
    assert deps.failures == []


async def test_a_filing_form_is_not_sent_through_status() -> None:
    invocation = _FakeInvocation()
    deps = _deps(invocation, action=_action("support"))
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_calls_then_answers("status"))):
        await agent.run("file it", deps=deps)

    assert invocation.read == []
    assert deps.raw_answers == []
