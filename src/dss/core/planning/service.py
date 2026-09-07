"""Planning behaviour (design v2 §6.6).

PLACEHOLDER for the plan-building logic: a real planner is an LLM that reads the
discovered capabilities, their domain schemas, the skills and the farmer's words,
and decides what to call and what is still missing. This stand-in is deterministic
— one step per ask that discovery could serve — so the workflow runs end to end
while the real Planner Agent is built by the team.

What is *not* a placeholder, and must survive into the real planner, is the
**barrier**: the moderation verdict is awaited here, at the last moment, before
the plan (which the executioner will act on) is returned. Everything up to this
point is read-only and discardable; nothing side-effecting runs until moderation
has cleared (design v2 §3, §6.6).
"""

from __future__ import annotations

from collections.abc import Awaitable

from dss.core.intent.models import Intent
from dss.core.moderation.models import ModerationDecision
from dss.core.planning.models import DomainSchemas, Plan, Step
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.core.skills.models import Skills
from dss.core.tool_discovery.models import ToolCandidates


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
    skills: Skills,
    discovered: DiscoveryResult,
    tools: ToolCandidates,
    schemas: DomainSchemas,
    verdict: Awaitable[ModerationDecision],
) -> Plan:
    # The barrier. A real planner builds the plan first and awaits this last; the
    # placeholder awaits it up front because it has no work to overlap. Either way
    # the plan is not returned — and so cannot be executed — until moderation
    # resolves.
    await verdict

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

    return Plan(
        steps=tuple(steps),
        skills=skills.selected,
        serves=tuple(serves),
        refused=(),  # moderation does not yet surface partial refusals
        missing=(),  # missing-input detection lands with the real planner
    )
