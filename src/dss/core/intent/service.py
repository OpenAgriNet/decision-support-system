"""The intent classifier.

One LLM call turns a turn into a ``IntentClassification`` — words only, no
geometry. The recent conversation goes into the prompt so a follow-up ("And
potato?") is read against what came before.

Turning that into an ``Intent`` — resolving each ask's ``place_name`` to a
``ResolvedPlace`` — is ``core.location``'s job. This module never builds an
``Ask``.

Plain Python: fills the prompt's slots, calls the ``PromptProvider`` and
``LLMProvider`` ports. The scheme catalog goes into the prompt too, so the
model names the scheme an ask is about (ADR-0021); only a code the catalog
holds is kept. The prompt's text lives under ``prompts/intent/``, one
version per language (configs/prompts.yaml), so this module decides *what the
template is told* — the taxonomy and the history window — never the wording.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.intent.models import (
    ClassifiedAsk,
    IntentClassification,
    InteractionType,
    Scheme,
    SchemeEntry,
    SubjectCategory,
)
from dss.core.shared.models import TextQuery, UserTurn
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptIdentifier, PromptProvider

# How many prior turns to show the model. Enough to resolve a reference without
# ballooning the prompt.
_HISTORY_WINDOW = 6


async def classify_intent(
    turn: UserTurn[TextQuery],
    llm: LLMProvider,
    prompts: PromptProvider,
    schemes: Sequence[SchemeEntry] = (),
) -> IntentClassification:
    """Classify the raw query in the context of the turn's recent history."""

    classification = await llm.structured(
        system_prompt=prompts.get_prompt(
            PromptIdentifier.INTENT,
            lang=turn.target_lang,
            placeholder_values={
                # The enums are the contract with the output schema, so the
                # template interpolates them rather than restating them — a
                # category added in code reaches every language's prompt.
                "categories": ", ".join(c.value for c in SubjectCategory),
                "interactions": ", ".join(i.value for i in InteractionType),
                "history": tuple(turn.history[-_HISTORY_WINDOW:]),
                "schemes": tuple(schemes),
            },
        ),
        user_query=turn.input.text,
        schema=IntentClassification,
    )
    by_code = {entry.scheme.code: entry.scheme for entry in schemes}
    return classification.model_copy(
        update={
            "asks": tuple(_with_scheme(ask, by_code) for ask in classification.asks)
        }
    )


def _with_scheme(ask: ClassifiedAsk, by_code: dict[str, Scheme]) -> ClassifiedAsk:
    """Put the catalog's scheme on the ask, or clear a code it does not hold.

    The subject becomes the official name: the planner fills a request with
    it and the composer names it back to the farmer.
    """

    scheme = by_code.get(ask.scheme_code) if ask.scheme_code else None
    if scheme is None:
        return ask.model_copy(update={"scheme_code": None, "scheme": None})
    return ask.model_copy(
        update={"agriculture_subjects": scheme.name, "scheme": scheme}
    )
