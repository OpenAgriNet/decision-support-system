"""The tool-calling agent loop that stands in for the planner/executioner
pair (ADR-0005). ``core/planner/`` does the work; this module wires it to
Pydantic AI.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from dss.core.moderation.models import Outcome
from dss.core.planner.describe_capability import render_candidates_as_markdown
from dss.core.planner.lookup import find_capability
from dss.core.planner.markdown import render_answer_as_markdown
from dss.core.planner.models import Failure, Skill, Verdict
from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.planner.skills import tool_names_for
from dss.core.planner.validation import (
    DomainSchema,
    InvalidArgument,
    validate_arguments,
)
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    FailureClass,
)
from dss.core.shared.models import UserTurn
from dss.ports.invocation import CapabilityInvocation, SelectFailed


@dataclass
class PlannerDeps:
    """Everything the agent's tools need for one turn. A plain object passed
    to ``agent.run(deps=...)`` — tools read it via ``RunContext.deps``."""

    turn: UserTurn
    discovery: DiscoveryResult
    schemas: dict[str, DomainSchema]
    schema_context_index: dict[str, str]
    invocation: CapabilityInvocation | None
    verdict: Verdict
    raw_answers: list[tuple[int, DiscoveredAnswer]] = field(default_factory=list)
    failures: list[tuple[int, Failure]] = field(default_factory=list)


async def _select(
    ctx: RunContext[PlannerDeps],
    ask_index: int,
    resource_id: str,
    resource_attributes: dict,
) -> str:
    """Read a chosen capability's data. ``ask_index`` and ``resource_id``
    together pick one candidate from discovery; ``resource_attributes``
    carries only what the model resolves or authors (a governed code, free
    text) — structural fields are assembled here, not by the model."""

    deps = ctx.deps
    decision = await deps.verdict.get()
    if decision.outcome is not Outcome.PROCEED:
        # Not a ModelRetry: retrying cannot clear a moderation rejection.
        return "This turn was not cleared to call providers. Do not retry."

    capability = find_capability(
        deps.discovery, ask_index=ask_index, resource_id=resource_id
    )
    if capability is None:
        raise ModelRetry(
            f"resource_id {resource_id!r} is not one of ask {ask_index}'s candidates"
        )

    schema = deps.schemas[capability.capability]
    try:
        validate_arguments(resource_attributes, schema)
    except InvalidArgument as exc:
        raise ModelRetry(str(exc)) from exc

    full_attributes = build_resource_attributes(
        capability=capability,
        turn=deps.turn,
        model_filled=resource_attributes,
        schema_context_index=deps.schema_context_index,
    )

    assert deps.invocation is not None
    try:
        answer = await deps.invocation.select(
            capability, full_attributes, deps.turn.transaction_id
        )
    except SelectFailed as exc:
        # The adapter has already retried and given up. Not a ModelRetry: the
        # arguments were fine, so re-sending them changes nothing and burns
        # the model's retry budget. Not a raise either — one unreachable
        # provider must not fail a turn whose other asks can still be served.
        deps.failures.append(
            (
                ask_index,
                Failure(
                    capability=exc.capability,
                    reason=exc.detail or str(exc.status_code),
                    retryable=exc.failure_class is FailureClass.TRANSIENT,
                ),
            )
        )
        return (
            f"Could not get an answer from {capability.provider_name} "
            f"({exc.detail or exc.status_code}). Try another candidate for "
            f"ask {ask_index} if there is one, or report that this ask has "
            f"no answer."
        )

    deps.raw_answers.append((ask_index, answer))
    return render_answer_as_markdown(answer)


async def _describe_capability(ctx: RunContext[PlannerDeps], ask_index: int) -> str:
    """Show an ask's candidates and each one's filterable fields, so the
    model knows what it may set before calling ``select``."""

    candidates = ctx.deps.discovery.capabilities.get(ask_index, ())
    return render_candidates_as_markdown(candidates, schemas=ctx.deps.schemas)


_TOOLS = {"select": _select, "describe_capability": _describe_capability}


def build_planner_agent(
    *,
    skills: Sequence[Skill],
    model: Model | str | None = None,
    system_prompt: str = "",
) -> Agent[PlannerDeps, str]:
    """Construct the planner agent, binding the union of the selected skills'
    ``tool_names`` (ADR-0006). An unselected skill's tools are never in the
    model's schema.

    A skill naming a tool that does not exist raises here rather than binding
    what it can: its guidance would tell the model to call a tool that is not
    there. Same stance as ``load_skills`` — do not boot on a broken config.

    ``name=name`` is required. Without it the tool registers under the Python
    function's own name (``_select``), and the model's call for ``select``
    comes back "Unknown tool name".

    ``model`` and ``system_prompt`` default to unset so a test can drive the
    agent with ``agent.override(model=...)`` and no prompt.
    """

    agent: Agent[PlannerDeps, str] = Agent(
        model, deps_type=PlannerDeps, system_prompt=system_prompt
    )
    for name in tool_names_for(skills):
        agent.tool(_TOOLS[name], name=name)
    return agent
