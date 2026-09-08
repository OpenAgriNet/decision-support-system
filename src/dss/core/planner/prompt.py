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

from dss.core.planner.markers import CONVERSATION, RETRIEVED_DATA, wrap_as_data
from dss.core.planner.models import Identity, Skill
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.shared.models import ConversationMessage

_REQUIRED_PLACEHOLDERS = (
    "{identity_name}",
    "{identity_persona}",
    "{identity_boundaries}",
    "{guidance_section}",
    "{answers_section}",
)


def _checked(template: str) -> str:
    """Every placeholder present, or raise.

    ``str.format`` drops an unknown placeholder silently, so a typo in the
    template would quietly ship a prompt with no identity or no guidance.

    The template is passed in, not read from disk. `core/` reaches nothing
    outside itself — a file read is a dependency, so loading lives in
    ``config/planner_prompt_loader.py`` (see tests/unit/test_core_isolation.py).
    """

    missing = [name for name in _REQUIRED_PLACEHOLDERS if name not in template]
    if missing:
        raise ValueError(
            f"the planner prompt template is missing {', '.join(missing)} — "
            "the rendered prompt would silently drop it"
        )
    return template


def _guidance_section(skills: Sequence[Skill]) -> str:
    if not skills:
        return (
            "# Guidance\n\n"
            "You have no skills loaded and no tools to call. Report that this "
            "turn cannot be served.\n\n"
        )
    body = "\n\n".join(skill.guidance.strip() for skill in skills)
    return f"# Guidance\n\n{body}\n\n"


def _answers_section(answers: dict[int, tuple[DiscoveredAnswer, ...]]) -> str:
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


def build_planner_prompt(
    *,
    identity: Identity,
    skills: Sequence[Skill],
    answers: dict[int, tuple[DiscoveredAnswer, ...]],
    template: str,
) -> str:
    """Render the system prompt from the shipped template.

    Static text lives in the template; what only the turn knows — the
    identity, the selected skills' guidance, the Direct answers — is filled
    in here. Nothing farmer- or network-supplied reaches this string.

    ``template`` is passed in rather than read here: `core/` reaches nothing
    outside itself, and a file read is a dependency. See
    ``config/planner_prompt_loader.py``."""

    return _checked(template).format(
        identity_name=identity.name,
        identity_persona=identity.persona,
        identity_boundaries=identity.boundaries,
        guidance_section=_guidance_section(skills),
        answers_section=_answers_section(answers),
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
