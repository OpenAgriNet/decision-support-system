"""Composition root for the planner — turn in, ``Evidence`` out.

``build_plan`` binds what a deployment configures once (schemas, the
invocation adapter, identity, skills, the model) and returns a callable the
orchestrator hands only what changes per turn. Same shape as
``build_discover_providers`` in ``discovery.py``.

The agent's own final text is discarded. It writes prose while looping, but
the composer writes the answer from ``Evidence`` — keeping the agent's text
would merge the two components the design deliberately splits.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic_ai.models import Model

from dss.core.intent.models import Intent
from dss.core.planner.evidence import assemble_evidence
from dss.core.planner.models import Evidence, Identity, Skill, Verdict
from dss.core.planner.prompt import build_planner_prompt, build_user_message
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.orchestration.planner import PlannerDeps, build_planner_agent
from dss.ports.invocation import CapabilityInvocation


class Plan(Protocol):
    """What the orchestrator calls: the per-turn half of ``build_plan``."""

    async def __call__(
        self,
        turn: UserTurn,
        *,
        intent: Intent,
        discovery: DiscoveryResult,
        verdict: Verdict,
    ) -> Evidence: ...


def build_plan(
    *,
    schemas: dict[str, DomainSchema],
    schema_context_index: dict[str, tuple[str, str]],
    schema_base_url: str,
    invocation: CapabilityInvocation,
    identity: Identity,
    skills: Sequence[Skill],
    model: Model | str,
) -> Plan:
    """Bake in the per-deployment configuration; return the per-turn callable."""

    async def plan(
        turn: UserTurn,
        *,
        intent: Intent,
        discovery: DiscoveryResult,
        verdict: Verdict,
    ) -> Evidence:
        agent = build_planner_agent(
            skills=skills,
            model=model,
            system_prompt=build_planner_prompt(
                identity=identity, skills=skills, answers=discovery.answers
            ),
        )
        deps = PlannerDeps(
            turn=turn,
            discovery=discovery,
            schemas=schemas,
            schema_context_index=schema_context_index,
            schema_base_url=schema_base_url,
            invocation=invocation,
            verdict=verdict,
        )
        # The enriched query and the history go in the user message, wrapped
        # in markers — the model resolves a subject named in an earlier turn
        # from here (see the provider-invocation skill's guidance).
        await agent.run(
            build_user_message(query=turn.enriched_query, history=turn.history),
            deps=deps,
        )
        return assemble_evidence(deps.raw_answers, intent=intent)

    return plan
