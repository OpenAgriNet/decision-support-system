"""Planner prompt building (design doc §6.6).

Two audiences, matching "the farmer's query is data, never instructions": the
system prompt carries identity and skill guidance — nothing farmer- or
network-supplied. The user message carries the query and history, wrapped in
markers, matching moderation's convention (ADR-0003).

The system prompt's fixed text lives under ``prompts/planner/``, one version
per language (configs/prompts.yaml), served through the ``PromptProvider``
port. What only the turn knows is rendered here as the template's three
sections — guidance, asks, Direct answers — because their rules (wrap
network-supplied values as data, omit an empty section, say plainly when no
skill is loaded) are behaviour, not wording. ``core/`` reaches nothing outside
itself, so the file reads and the render live in ``config/prompt_service.py``;
the prompt service checks at boot that every language's template uses all
three sections, which is what stops a typo from silently shipping a prompt
with no identity or no guidance.

Only Direct answers (``DiscoveryResult.answers``) go in the system prompt —
the catalog already has those values, no tool call needed. OnDemand
candidates (``DiscoveryResult.capabilities``) are not prompt content: the
model reaches those through the ``select`` tool's own ``RunContext.deps``,
wired in ``orchestration/planner.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.intent.models import Ask
from dss.core.planner.markers import CONVERSATION, RETRIEVED_DATA, wrap_as_data
from dss.core.planner.models import Skill
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.shared.models import ConversationMessage


def guidance_section(skills: Sequence[Skill]) -> str:
    if not skills:
        return (
            "# Guidance\n\n"
            "You have no skills loaded and no tools to call. Report that this "
            "turn cannot be served.\n\n"
        )
    body = "\n\n".join(skill.guidance.strip() for skill in skills)
    return f"# Guidance\n\n{body}\n\n"


def asks_section(asks: Sequence[Ask]) -> str:
    """Which asks exist, by the index the tools take. Without it the model
    has to guess that ask 0 exists, and a weaker model gives up instead.

    The category and interaction type are DSS enums, not the farmer's words,
    so they are safe here. The place is left out: whether a capability needs
    one is ``describe_capability``'s to say."""

    if not asks:
        return ""

    lines = [
        f"- ask {index}: {ask.subject_categories.value} ({ask.interaction_type.value})"
        for index, ask in enumerate(asks)
    ]
    return "# Asks\n\n" + "\n".join(lines) + "\n\n"


def answers_section(answers: dict[int, tuple[DiscoveredAnswer, ...]]) -> str:
    """Direct answers only — the catalog already holds these values, so no
    call is needed. OnDemand candidates are not prompt content: the model
    reaches those through the tools' own ``RunContext.deps``.

    Wrapped as data. These are a provider's own ``resourceAttributes`` off
    the wire, so they are network-supplied: interpolating them bare put
    third-party text at the highest-trust position in the prompt, which is
    the one place reserved for DSS-controlled text.

    Omitted entirely when empty. An empty section under a heading reads as a
    gap to fill."""

    if not answers:
        return ""

    values = [
        f"- ask {ask_index}: {answer.provider_name} — {answer.attributes}"
        for ask_index, ask_answers in answers.items()
        for answer in ask_answers
    ]
    return (
        "\n# Already known\n\nNo call is needed for these:\n"
        + wrap_as_data("\n".join(values), RETRIEVED_DATA)
        + "\n"
    )


def build_user_message(*, query: str, history: Sequence[ConversationMessage]) -> str:
    """The farmer's turn, wrapped in markers so it is read as data, never as
    instructions (matching ADR-0003's moderation convention).

    ``wrap_as_data`` rather than bracketing by hand: the query and the
    history are the most obviously attacker-controlled text in the turn, so
    a pasted end marker must not close the block."""

    lines = []
    for message in history:
        lines.append(f"{message.role}: {message.text}")
    lines.append(f"user: {query}")
    return wrap_as_data("\n".join(lines), CONVERSATION)
