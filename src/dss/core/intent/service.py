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

from dss.core.intent.models import Capability, Intent, SubjectCategory
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
    capabilities = ", ".join(c.value for c in Capability)
    lines = [
        "You are an intent classifier for an agriculture assistant.",
        "Classify the user's latest query along three axes:",
        f"- subject_categories: zero or more of [{categories}].",
        "- agriculture_subjects: free-text specifics named in the query "
        "(e.g. 'potato', 'wheat rust'); [] if none.",
        f"- capabilities: [{capabilities}] — Knowledge to answer/advise, "
        "Service to perform an action (book, register, apply).",
        "",
        "If the query is a follow-up ('And potato?', 'Is it safe to use?'), "
        "resolve it against the conversation before classifying.",
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
