"""The orchestrator — one function that runs a whole turn (design v2 §4).

The orchestrator holds the turn and calls each component in order. Components
never call each other: every one takes plain objects and returns plain objects,
and this module is the only place that knows the sequence. Order is code, not
config — the shape of ``run_turn`` *is* the pipeline.

    classify ─┬─ skills ───────┐
              ├─ providers ─────┤
              ├─ tools ─────────┤  (fan out — all read-only, all discardable)
    moderate ─┘                 │
                                ▼
                             plan  ← awaits the moderation verdict here: the
                                │     barrier. Nothing side-effecting has run yet.
                                ▼
                         (gate on outcome — only PROCEED streams)
                                ▼
                          execute → compose → [review] → shape → chunks

Everything above ``plan`` can be thrown away; from ``execute`` onward a Provider
call may already have happened and cannot be. That barrier is why moderation is
awaited at the last moment before execution, and why the four non-answer outcomes
short-circuit before any outside call (design v2 §3).

Composition, not config, decides *which* components run: the callables are wired
once in :func:`build_components` (or a test), and optional ones — the Response
Reviewer today — are simply left unset. This module never imports the
orchestration framework; it composes plain async callables behind ``ports``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from dss.core.channel.models import ChannelChunk, Status
from dss.core.channel.service import shape as shape_service
from dss.core.composition.models import Answer, Identity
from dss.core.composition.service import compose as compose_service
from dss.core.execution.models import Evidence
from dss.core.execution.service import execute as execute_service
from dss.core.intent.models import Intent
from dss.core.intent.service import classify_intent
from dss.core.moderation.models import ModerationContext, ModerationDecision, Outcome
from dss.core.moderation.service import moderate as moderate_service
from dss.core.planning.models import DomainSchemas, Plan
from dss.core.planning.service import plan_turn
from dss.core.policy.models import Policy
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.review.models import ReviewVerdict
from dss.core.review.service import review as review_service
from dss.core.shared.models import UserTurn
from dss.core.skills.models import Skills
from dss.core.skills.service import discover_skills as discover_skills_service
from dss.core.tool_discovery.models import ToolCandidates
from dss.core.tool_discovery.service import discover_tools as discover_tools_service
from dss.orchestration.discovery import DiscoverProviders
from dss.ports.llm import LLMProvider
from dss.ports.tools import ToolIndex

logger = logging.getLogger(__name__)

# What the three non-answer outcomes say. One message, then the stream stops. Real
# wording is the composer's job, in target_lang — this is a placeholder until the
# terminal path routes through compose/shape too.
_TERMINAL_TEXT: dict[Status, str] = {
    Status.REJECTED: "This request can't be answered.",
    Status.NEEDS_CLARIFICATION: "Could you share a little more so I can help?",
    Status.NO_MATCH: "I couldn't find anything that can answer this.",
}


@dataclass(frozen=True)
class OrchestratorComponents:
    """The components a turn runs through, each already bound to its own model,
    ports and config. Swapping an implementation — or leaving ``review`` unset —
    is a change here, not in ``run_turn``."""

    classify: Callable[[UserTurn], Awaitable[Intent]]
    moderate: Callable[[UserTurn], Awaitable[ModerationDecision]]
    discover_skills: Callable[[UserTurn, Intent], Awaitable[Skills]]
    discover_providers: Callable[
        [Intent, UserTurn, datetime], Awaitable[DiscoveryResult]
    ]
    discover_tools: Callable[[Intent], Awaitable[ToolCandidates]]
    plan: Callable[
        [
            UserTurn,
            Intent,
            Skills,
            DiscoveryResult,
            ToolCandidates,
            Awaitable[ModerationDecision],
        ],
        Awaitable[Plan],
    ]
    execute: Callable[[Plan], Awaitable[Evidence]]
    compose: Callable[[Evidence, Plan, Identity], Awaitable[Answer]]
    shape: Callable[[Answer, UserTurn], AsyncIterator[ChannelChunk]]
    identity: Identity
    # Optional (design v2 §6.9). Unset → review is skipped.
    review: Callable[[Answer, Evidence], Awaitable[ReviewVerdict]] | None = None


def status_for(decision: ModerationDecision, plan: Plan) -> Status:
    """Which of the four ways out this turn took. Only the orchestrator can decide
    this — it is the only thing that sees both the moderation verdict and the plan
    (design v2 §3). Moderation's outcome wins; on ``PROCEED`` the plan's
    steps/missing pair decides (the table in §6.6)."""

    if decision.outcome is Outcome.REJECT:
        return Status.REJECTED
    if decision.outcome is Outcome.CLARIFY:
        return Status.NEEDS_CLARIFICATION
    if decision.outcome is Outcome.NO_MATCH:
        return Status.NO_MATCH
    # PROCEED.
    if plan.steps:
        return Status.ANSWERED  # run it (missing inputs, if any, are asked alongside)
    if plan.missing:
        return Status.NEEDS_CLARIFICATION  # pure clarification
    return Status.NO_MATCH  # nothing to do


async def run_turn(
    turn: UserTurn,
    components: OrchestratorComponents,
    *,
    now: datetime,
) -> AsyncIterator[ChannelChunk]:
    """Run one turn end to end, streaming channel chunks.

    ``now`` is passed in rather than read from the clock so the turn is
    reproducible and testable (the repo threads time explicitly). Only an
    ``ANSWERED`` turn streams; the other three emit one chunk and stop.
    """

    # Moderation needs only the turn, so it starts alongside intent and runs while
    # discovery fans out (ADR-0003). It is handed to the planner as an awaitable
    # and awaited at the barrier — see below.
    moderation_task: asyncio.Task[ModerationDecision] = asyncio.ensure_future(
        components.moderate(turn)
    )
    try:
        intent = await components.classify(turn)

        # Fan out — skills, providers and tools all need only the intent, and all
        # are read-only/local, so they run together and can be thrown away.
        skills, discovered, tools = await asyncio.gather(
            components.discover_skills(turn, intent),
            components.discover_providers(intent, turn, now),
            components.discover_tools(intent),
        )

        # The planner builds a data-only plan and awaits the moderation verdict at
        # the barrier. When it returns, moderation has resolved.
        plan = await components.plan(
            turn, intent, skills, discovered, tools, moderation_task
        )
        decision = await moderation_task
    except BaseException:
        # If we bailed before the verdict was consumed (e.g. intent raised), don't
        # leak the moderation task or its exception.
        moderation_task.cancel()
        with contextlib.suppress(BaseException):
            await moderation_task
        raise

    status = status_for(decision, plan)
    if status is not Status.ANSWERED:
        # One message, then stop — no execution, so no side-effecting call happens.
        yield ChannelChunk(text=_TERMINAL_TEXT[status], is_final=True)
        return

    # Past the barrier: outside calls may now happen and cannot be undone.
    evidence = await components.execute(plan)
    answer = await components.compose(evidence, plan, components.identity)

    if components.review is not None:
        # Does not block the stream — logs / flags for eval (design v2 §6.9).
        verdict = await components.review(answer, evidence)
        if not verdict.grounded:
            logger.warning(
                "transaction_id=%s response_review_ungrounded=True violations=%d",
                turn.transaction_id,
                len(verdict.violations),
            )

    async for chunk in components.shape(answer, turn):
        yield chunk


def build_components(
    *,
    intent_llm: LLMProvider,
    moderation_llm: LLMProvider,
    policies: Sequence[Policy],
    discover_providers: DiscoverProviders,
    tool_index: ToolIndex,
    identity: Identity,
    domain_schemas: DomainSchemas | None = None,
    enable_review: bool = False,
) -> OrchestratorComponents:
    """Wire the real components (intent, moderation, provider discovery) together
    with the placeholders for the pieces still being built, binding each to its
    model, ports and config. The entrypoint calls this once at startup; ``run_turn``
    then reuses the bundle for every turn.
    """

    schemas = domain_schemas or DomainSchemas()

    async def moderate(turn: UserTurn) -> ModerationDecision:
        return await moderate_service(
            ModerationContext(turn=turn), policies, moderation_llm
        )

    async def plan(
        turn: UserTurn,
        intent: Intent,
        skills: Skills,
        discovered: DiscoveryResult,
        tools: ToolCandidates,
        verdict: Awaitable[ModerationDecision],
    ) -> Plan:
        return await plan_turn(
            turn, intent, skills, discovered, tools, schemas, verdict
        )

    if not enable_review:
        logger.info(
            "response_review_disabled=True — grounding violations will not be detected"
        )

    return OrchestratorComponents(
        classify=partial(classify_intent, llm=intent_llm),
        moderate=moderate,
        discover_skills=discover_skills_service,
        discover_providers=discover_providers,
        discover_tools=partial(discover_tools_service, index=tool_index),
        plan=plan,
        execute=execute_service,
        compose=compose_service,
        shape=shape_service,
        identity=identity,
        review=review_service if enable_review else None,
    )
