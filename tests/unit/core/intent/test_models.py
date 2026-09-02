"""Tier 1 — the Intent and UserTurn contracts (spec 0002)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.intent.models import Capability, Intent, SubjectCategory
from dss.core.shared.models import UserTurn


def test_intent_defaults_are_empty() -> None:
    intent = Intent()
    assert intent.subject_categories == []
    assert intent.agriculture_subjects == []
    assert intent.capabilities == []


def test_intent_accepts_the_three_axes() -> None:
    intent = Intent(
        subject_categories=[SubjectCategory.MARKET],
        agriculture_subjects=["potato"],
        capabilities=[Capability.KNOWLEDGE],
    )
    assert intent.subject_categories == [SubjectCategory.MARKET]
    assert intent.agriculture_subjects == ["potato"]
    assert intent.capabilities == [Capability.KNOWLEDGE]


def test_subject_category_values() -> None:
    assert {c.value for c in SubjectCategory} == {
        "Crop",
        "Livestock",
        "Weather",
        "Market",
        "Scheme",
    }


def test_capability_values() -> None:
    assert {c.value for c in Capability} == {"Knowledge", "Service"}


def test_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(primary_domian="dairy")


def test_unknown_subject_category_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(subject_categories=["Fishery"])


def test_intent_is_frozen() -> None:
    intent = Intent()
    with pytest.raises(ValidationError):
        intent.subject_categories = [SubjectCategory.CROP]


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
