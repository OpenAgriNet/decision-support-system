"""Tier 3 — the support tool wired into a real Pydantic AI Agent.

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
from dss.ports.invocation import SupportFailedError

PROVIDER = ProviderCapability(
    provider_id="pmfby",
    provider_name="PMFBY Grievance Portal",
    capability="openagrinet:Grievance",
    resource_id="res:pmfby:grievance",
    observed_categories=("Grievance",),
)

PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")

ACTION = ActionSchema(
    context=PMFBY_SUPPORT,
    must_send=(
        ActionField(
            path="informationMode",
            source=FieldSource.FIXED,
            allowed_values=("OnDemand",),
        ),
        ActionField(
            path="scheme.code", source=FieldSource.FIXED, allowed_values=("PMFBY",)
        ),
        ActionField(path="provider.id", source=FieldSource.PROVIDER),
        ActionField(path="provider.descriptor.name", source=FieldSource.PROVIDER),
        ActionField(
            path="applicantMobile", source=FieldSource.FARMER, pii_class="contact"
        ),
        ActionField(
            path="grievance.description",
            source=FieldSource.FARMER,
            pii_class="freetext",
        ),
    ),
    jsonld={"@type": "openagrinet:Grievance"},
)

PHONE = "9876543210"
VALUES = {"applicantMobile": PHONE, "grievance.description": "No payment yet."}

FILED = {
    "@type": "openagrinet:Grievance",
    "scheme": {"code": "PMFBY"},
    "case": {"ticketNo": "100626000099001", "status": {"code": "Registered"}},
}


class _FakeInvocation:
    def __init__(self) -> None:
        self.filed: list[dict] = []

    async def support(self, capability, attributes, transaction_id, *, visibility=None):
        self.filed.append(attributes)
        return ActionReply(attributes=FILED)

    async def select(self, *args, **kwargs):
        raise AssertionError("select must not be called for a filing")


def _skill() -> Skill:
    return Skill(
        id="provider-action",
        domain="agriculture",
        description="File a form with a provider.",
        guidance="call support once",
        tool_names=("describe_capability", "support"),
    )


def _deps(invocation: _FakeInvocation) -> PlannerDeps:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    ask = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
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
                definition=ACTION.form(), submission_id="submission-1", values=VALUES
            ),
            action=ACTION,
        ),
    )


def _files_then_answers(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    if len(messages) == 1:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="support",
                    args={"ask_index": 0, "resource_id": PROVIDER.resource_id},
                )
            ]
        )
    return ModelResponse(parts=[TextPart(content="filed")])


async def test_support_files_the_form_with_the_chosen_provider() -> None:
    invocation = _FakeInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_files_then_answers)):
        await agent.run("file it", deps=deps)

    (body,) = invocation.filed
    assert body["applicantMobile"] == PHONE
    assert body["provider"] == {
        "id": "pmfby",
        "descriptor": {"name": "PMFBY Grievance Portal"},
    }
    assert body["informationMode"] == "OnDemand"
    [(ask_index, answer)] = deps.raw_answers
    assert ask_index == 0
    assert answer.attributes["case"]["ticketNo"] == "100626000099001"
    assert deps.failures == []


class _RefusingInvocation(_FakeInvocation):
    async def support(self, capability, attributes, transaction_id, *, visibility=None):
        self.filed.append(attributes)
        raise SupportFailedError(
            capability.capability, 503, FailureClass.TRANSIENT, retry_after_seconds=120
        )


async def test_a_failed_filing_is_a_retryable_failure_with_the_providers_time() -> None:
    invocation = _RefusingInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_files_then_answers)):
        await agent.run("file it", deps=deps)

    assert deps.raw_answers == []
    (failure,) = deps.failures
    assert failure.ask_index == 0
    assert failure.capability == "openagrinet:Grievance"
    assert failure.retryable is True
    assert failure.retry_after_seconds == 120


def _files_twice_then_answers(
    messages: list[ModelMessage], info: AgentInfo
) -> ModelResponse:
    # Each tool call adds the response and the tool's return: two calls are
    # in the history once it holds five messages.
    if len(messages) < 5:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="support",
                    args={"ask_index": 0, "resource_id": PROVIDER.resource_id},
                )
            ]
        )
    return ModelResponse(parts=[TextPart(content="filed")])


async def test_a_form_is_filed_once_however_often_the_model_asks() -> None:
    invocation = _FakeInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_files_twice_then_answers)):
        await agent.run("file it", deps=deps)

    assert len(invocation.filed) == 1
    assert len(deps.raw_answers) == 1


async def test_a_failed_filing_is_not_sent_again_however_often_the_model_asks() -> None:
    invocation = _RefusingInvocation()
    deps = _deps(invocation)
    agent = build_planner_agent(skills=[_skill()])

    with agent.override(model=FunctionModel(_files_twice_then_answers)):
        await agent.run("file it", deps=deps)

    assert len(invocation.filed) == 1
    assert len(deps.failures) == 1
