"""Tier 1 — the Intent and UserTurn contracts (spec 0002)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.intent.models import ActionType, Intent
from dss.core.shared.models import UserTurn


def test_confidence_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(confidence=1.5)


def test_intent_defaults_are_empty() -> None:
    intent = Intent(confidence=0.5)
    assert intent.domains == []
    assert intent.action_types == []


def test_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(confidence=0.5, primary_domian="dairy")


def test_intent_is_frozen() -> None:
    intent = Intent(confidence=0.5)
    with pytest.raises(ValidationError):
        intent.confidence = 0.9


def test_action_type_values() -> None:
    assert {a.value for a in ActionType} == {"advisory", "lookup", "act"}


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
