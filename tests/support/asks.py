from __future__ import annotations

from typing import Any

from dss.core.intent.models import Ask, InteractionType, SubjectCategory


def an_ask(**overrides: Any) -> Ask:
    """A weather question with no place, unless a field is overridden. Tests
    build asks here so a new required field on `Ask` is set in one place."""

    fields: dict[str, Any] = {
        "id": 0,
        "subject_categories": SubjectCategory.WEATHER,
        "interaction_type": InteractionType.OBSERVE,
    }
    return Ask(**(fields | overrides))


def numbered(*asks: Ask) -> tuple[Ask, ...]:
    """The same asks with ids 0, 1, 2 in the order given: one turn's asks."""

    return tuple(
        ask.model_copy(update={"id": position}) for position, ask in enumerate(asks)
    )
