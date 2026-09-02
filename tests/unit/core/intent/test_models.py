"""Tier 1 — the Intent and UserTurn contracts (spec 0002)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    SubjectCategory,
)
from dss.core.shared.models import UserTurn


def test_intent_defaults_are_empty() -> None:
    intent = Intent()
    assert intent.asks == ()
    assert intent.confidence == 0.0


def test_intent_carries_asks_and_confidence() -> None:
    ask = Ask(
        agriculture_subjects="potato",
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    assert intent.asks == (ask,)
    assert intent.confidence == 0.9


def test_ask_subject_may_be_none() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
    )
    assert ask.agriculture_subjects is None


def test_interaction_type_values() -> None:
    assert {i.value for i in InteractionType} == {"advise", "observe", "act"}


def test_subject_category_values() -> None:
    assert {c.value for c in SubjectCategory} == {
        "Crop",
        "Livestock",
        "Weather",
        "Market",
        "Scheme",
    }


def test_confidence_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(confidence=1.5)


def test_unknown_subject_category_raises() -> None:
    with pytest.raises(ValidationError):
        Ask(subject_categories="Fishery", interaction_type=InteractionType.ADVISE)


def test_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(primary_domian="dairy")


def test_intent_is_frozen() -> None:
    intent = Intent()
    with pytest.raises(ValidationError):
        intent.confidence = 0.9


def test_user_turn_requires_both_query_fields() -> None:
    with pytest.raises(ValidationError):
        UserTurn(
            original_query="hi",
            session_id="s",
            source_lang="en",
            target_lang="en",
            channel="web",
        )


def test_non_bcp47_language_raises() -> None:
    with pytest.raises(ValidationError):
        UserTurn(
            original_query="hi",
            enriched_query="hi",
            session_id="s",
            source_lang="gujarati",  # full name, not a code
            target_lang="en",
            channel="web",
        )
