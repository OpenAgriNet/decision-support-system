from __future__ import annotations

from typing import Any

from dss.core.intent.models import Ask, InteractionType, SubjectCategory


def an_ask(**overrides: Any) -> Ask:
    """A weather question with no place, unless a field is overridden. Tests
    build asks here so a new required field on `Ask` is set in one place."""

    fields: dict[str, Any] = {
        "subject_categories": SubjectCategory.WEATHER,
        "interaction_type": InteractionType.OBSERVE,
    }
    return Ask(**(fields | overrides))
