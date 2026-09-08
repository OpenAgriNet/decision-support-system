"""The planner agent (design v2 §6.6).

Discovery says who could answer; the planner decides what to actually call, in what
order, and what is still missing — and, in this codebase, owns execution too: plan
creation and running the plan live in the one agent, so there is no separate
executioner step. The orchestrator hands it a turn that moderation has already
cleared, so everything it does is past the barrier.

PLACEHOLDER for the agent itself: a real planner is an LLM that reads the
discovered capabilities and their schemas and builds (and runs) the plan. This
stand-in is deterministic — one step per ask discovery could serve — so the
orchestrator wiring can be exercised while the real agent is built by the team.
"""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.moderation.models import ModerationDecision
from dss.core.planning.models import Plan, Step
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn


def _capability_for(discovered: DiscoveryResult, ask_index: int) -> str | None:
    """The @type this ask can be served by, if any — a Direct answer or an
    OnDemand capability. ``None`` means nobody serves the ask (no step, no_match)."""

    answers = discovered.answers.get(ask_index, ())
    if answers:
        return answers[0].capability
    capabilities = discovered.capabilities.get(ask_index, ())
    if capabilities:
        return capabilities[0].capability
    return None


async def plan_turn(
    turn: UserTurn,
    intent: Intent,
    discovered: DiscoveryResult,
    decision: ModerationDecision,
) -> Plan:
    steps: list[Step] = []
    serves: list[int] = []
    for ask_index in range(len(intent.asks)):
        capability = _capability_for(discovered, ask_index)
        if capability is None:
            continue  # an ask nobody can answer gets no step (design v2 §6.4)
        ask = intent.asks[ask_index]
        inputs = (
            {"subject": ask.agriculture_subjects} if ask.agriculture_subjects else {}
        )
        steps.append(Step(id=len(steps) + 1, capability=capability, inputs=inputs))
        serves.append(ask_index)

    return Plan(steps=tuple(steps), serves=tuple(serves), refused=(), missing=())
