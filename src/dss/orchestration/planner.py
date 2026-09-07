"""The tool-calling agent loop that stands in for the planner/executioner
pair (ADR-0005). ``core/planner/`` does the work; this module wires it to
Pydantic AI.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext

from dss.core.planner.describe_capability import render_candidates_as_markdown
from dss.core.planner.lookup import find_capability
from dss.core.planner.markdown import render_answer_as_markdown
from dss.core.planner.resource_attributes import build_resource_attributes
from dss.core.planner.validation import (
    DomainSchema,
    InvalidArgument,
    validate_arguments,
)
from dss.core.provider_discovery.models import DiscoveredAnswer, DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.ports.invocation import CapabilityInvocation


@dataclass
class PlannerDeps:
    """Everything the agent's tools need for one turn. A plain object passed
    to ``agent.run(deps=...)`` — tools read it via ``RunContext.deps``."""

    turn: UserTurn
    discovery: DiscoveryResult
    schemas: dict[str, DomainSchema]
    schema_context_index: dict[str, tuple[str, str]]
    schema_base_url: str
    invocation: CapabilityInvocation | None
    raw_answers: list[tuple[int, DiscoveredAnswer]] = field(default_factory=list)


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
        schema_base_url=deps.schema_base_url,
    )

    assert deps.invocation is not None
    answer = await deps.invocation.select(
        capability, full_attributes, deps.turn.transaction_id
    )
    deps.raw_answers.append((ask_index, answer))
    return render_answer_as_markdown(answer)


async def _describe_capability(ctx: RunContext[PlannerDeps], ask_index: int) -> str:
    """Show an ask's candidates and each one's filterable fields, so the
    model knows what it may set before calling ``select``."""

    candidates = ctx.deps.discovery.capabilities.get(ask_index, ())
    return render_candidates_as_markdown(candidates, schemas=ctx.deps.schemas)


_TOOLS = {"select": _select, "describe_capability": _describe_capability}


def build_planner_agent(*, tool_names: Sequence[str]) -> Agent[PlannerDeps, str]:
    """Construct the planner agent, binding only the tools named in
    ``tool_names`` — the union of every selected skill's ``tool_names``
    (ADR-0006). A tool absent from this set is never in the model's schema.
    """

    agent: Agent[PlannerDeps, str] = Agent(deps_type=PlannerDeps)
    for name in tool_names:
        agent.tool(_TOOLS[name], name=name)
    return agent
