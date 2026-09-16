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

from dss.config.planner_prompt_loader import load_planner_prompt_template
from dss.core.intent.models import Intent
from dss.core.planner.evidence import assemble_evidence
from dss.core.planner.models import Evidence, Identity, Skill, Verdict
from dss.core.planner.prompt import build_planner_prompt, build_user_message
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.observability.trace_log import log_external_response
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
    schema_context_index: dict[str, str],
    invocation: CapabilityInvocation,
    identity: Identity,
    skills: Sequence[Skill],
    model: Model | str,
    temperature: float = 0.0,
    timeout_seconds: float = 30.0,
    retries: int = 3,
) -> Plan:
    """Bake in the per-deployment configuration; return the per-turn callable.

    The planner binds its own model settings (ADR-0004: each component does).
    A longer timeout than the single-shot components, because one run is
    several model round-trips plus the provider calls between them; and more
    retries than the framework's default of 1, because the design raises
    ``ModelRetry`` in three places.
    """

    model_settings = {"temperature": temperature, "timeout": timeout_seconds}
    # Read once at wiring time, not per turn: the template is a shipped
    # constant, and `core/` may not read files at all.
    prompt_template = load_planner_prompt_template()

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
            retries=retries,
            system_prompt=build_planner_prompt(
                identity=identity,
                skills=skills,
                answers=discovery.answers,
                template=prompt_template,
            ),
        )
        deps = PlannerDeps(
            turn=turn,
            intent=intent,
            discovery=discovery,
            schemas=schemas,
            schema_context_index=schema_context_index,
            invocation=invocation,
            verdict=verdict,
        )
        # The enriched query and the history go in the user message, wrapped
        # in markers — the model resolves a subject named in an earlier turn
        # from here (see the provider-invocation skill's guidance).
        result = await agent.run(
            build_user_message(query=turn.enriched_query, history=turn.history),
            deps=deps,
            model_settings=model_settings,
        )
        # The planner's own prose is discarded downstream (the composer writes
        # the answer), but log it so the model's final say is visible next to
        # the provider calls its `select` tool made.
        log_external_response("llm.planner", turn.transaction_id, body=result.output)
        # Direct answers too: they need no select call, so they never land in
        # raw_answers, and leaving them out lost an ask the catalog had
        # already answered.
        return assemble_evidence(
            deps.raw_answers,
            intent=intent,
            direct_answers=discovery.answers,
            failures=deps.failures,
        )

    return plan
