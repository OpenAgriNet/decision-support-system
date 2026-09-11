"""The intent classifier (spec 0002).

One batched, structured LLM call maps a turn to an ``Intent``. The recent
conversation is rendered into the prompt so a follow-up ("And potato?") is
classified against what came before rather than in isolation.

Framework-agnostic: this builds a plain prompt and depends only on the
``LLMProvider`` port. The adapter turns the returned ``Intent`` schema into
structured output.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.intent.models import Intent, InteractionType, SubjectCategory
from dss.core.shared.models import ConversationMessage, UserTurn
from dss.ports.llm import LLMProvider

# How many prior turns to show the model. Enough to resolve a reference without
# ballooning the prompt.
_HISTORY_WINDOW = 6


def _render_history(history: Sequence[ConversationMessage]) -> list[str]:
    if not history:
        return []
    lines = ["", "Conversation so far (oldest first):"]
    for message in history[-_HISTORY_WINDOW:]:
        lines.append(f"  {message.role}: {message.text}")
    return lines


def build_intent_prompt(history: Sequence[ConversationMessage]) -> str:
    """Render the intent taxonomy and any conversation context into one prompt."""

    categories = ", ".join(c.value for c in SubjectCategory)
    interactions = ", ".join(i.value for i in InteractionType)
    lines = [
        "You are an intent classifier for an agriculture assistant.",
        "Break the user's latest query into one or more asks. A query may hold "
        "several (e.g. 'wheat price and will it rain?' is two asks).",
        "Each ask has:",
        f"- subject_categories: exactly one of [{categories}].",
        f"- interaction_type: one of [{interactions}] — advise to explain/guide, "
        "observe to look up a value/record/status, act to perform an action "
        "(book, apply, submit, update, escalate).",
        "- agriculture_subjects: the free-text specific named "
        "(e.g. 'potato', 'PM-KISAN'); null when the category needs none "
        "(e.g. 'will it rain?').",
        "",
        "Also return an overall confidence in [0, 1].",
        "If the query is a follow-up ('And potato?', 'Is it safe to use?'), "
        "resolve it against the conversation before classifying.",
        "",
        "place_name: the place the user says they are in or asks about, "
        "written in English (transliterate: 'मी पुण्याहून' -> 'Pune'). "
        "Return the place only — no district/taluka/village word, no state, "
        "no coordinates. Use null if no place is named; do not guess one from "
        "the crop, the language, or the conversation's subject.",
    ]
    lines += _render_history(history)
    return "\n".join(lines)


async def classify_intent(turn: UserTurn, llm: LLMProvider) -> Intent:
    """Classify the raw query in the context of the turn's recent history."""

    return await llm.structured(
        system_prompt=build_intent_prompt(turn.history),
        user_query=turn.original_query,
        schema=Intent,
    )
