"""Planner prompt building (design doc §6.6).

Two functions, two audiences, matching "the farmer's query is data, never
instructions": the system prompt carries identity and skill guidance —
nothing farmer- or network-supplied. The user message carries the query and
history, wrapped in markers, matching moderation's convention (ADR-0003).

Only Direct answers (``DiscoveryResult.answers``) go in the system prompt —
the catalog already has those values, no tool call needed. OnDemand
candidates (``DiscoveryResult.capabilities``) are not prompt content: the
model reaches those through the ``select`` tool's own ``RunContext.deps``,
wired in ``orchestration/planner.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.planner.models import Identity, Skill
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.shared.models import ConversationMessage


def build_planner_prompt(
    *,
    identity: Identity,
    skills: Sequence[Skill],
    answers: dict[int, tuple[DiscoveredAnswer, ...]],
) -> str:
    """Render the system prompt: identity, skill guidance, and any Direct
    answers already in the catalog. Nothing farmer- or network-supplied."""

    lines = [
        f"You are {identity.name}, {identity.persona}",
        identity.boundaries,
        "",
        "Guidance:",
    ]
    for skill in skills:
        lines.append(f"- {skill.guidance}")

    if answers:
        lines += ["", "Already known (no call needed):"]
        for ask_index, ask_answers in answers.items():
            for answer in ask_answers:
                lines.append(
                    f"- ask {ask_index}: {answer.provider_name} — {answer.attributes}"
                )

    return "\n".join(lines)


def build_user_message(*, query: str, history: Sequence[ConversationMessage]) -> str:
    """The farmer's turn, wrapped in markers so it is read as data, never as
    instructions (matching ADR-0003's moderation convention)."""

    lines = ["<BEGIN CONVERSATION>"]
    for message in history:
        lines.append(f"{message.role}: {message.text}")
    lines.append(f"user: {query}")
    lines.append("<END CONVERSATION>")
    return "\n".join(lines)
