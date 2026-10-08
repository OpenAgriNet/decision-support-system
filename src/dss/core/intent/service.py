"""The intent classifier.

One LLM call turns a turn into a ``IntentClassification`` — words only, no
geometry. The recent conversation goes into the prompt so a follow-up ("And
potato?") is read against what came before.

Turning that into an ``Intent`` — resolving each ask's ``place_name`` to a
``ResolvedPlace`` — is ``core.location``'s job. This module never builds an
``Ask``.

Plain Python: fills the prompt's slots, calls the ``PromptProvider`` and
``LLMProvider`` ports. The prompt's text lives under ``prompts/intent/``, one
version per language (configs/prompts.yaml), so this module decides *what the
template is told* — the taxonomy and the history window — never the wording.
"""

from __future__ import annotations

from dss.core.intent.models import (
    IntentClassification,
    InteractionType,
    SubjectCategory,
)
from dss.core.shared.models import UserTurn
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider

# How many prior turns to show the model. Enough to resolve a reference without
# ballooning the prompt.
_HISTORY_WINDOW = 6


async def classify_intent(
    turn: UserTurn, llm: LLMProvider, prompts: PromptProvider
) -> IntentClassification:
    """Classify the raw query in the context of the turn's recent history."""

    return await llm.structured(
        system_prompt=prompts.get_prompt(
            "INTENT",
            lang=turn.target_lang,
            kwargs={
                # The enums are the contract with the output schema, so the
                # template interpolates them rather than restating them — a
                # category added in code reaches every language's prompt.
                "categories": ", ".join(c.value for c in SubjectCategory),
                "interactions": ", ".join(i.value for i in InteractionType),
                "history": tuple(turn.history[-_HISTORY_WINDOW:]),
            },
        ),
        user_query=turn.original_query,
        schema=IntentClassification,
    )
