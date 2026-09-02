"""Tier-1 unit tests for the intent domain types and the taxonomy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.intent import BASE_TAXONOMY, ActionType, Ask, Intent, Taxonomy


def test_base_taxonomy_is_the_shipped_category_set():
    assert BASE_TAXONOMY.categories == (
        "Crop",
        "Livestock",
        "Weather",
        "Market",
        "Scheme",
        "Knowledge",
        "Service",
    )


def test_taxonomy_resolve_is_case_and_whitespace_insensitive():
    assert BASE_TAXONOMY.resolve("  market ") == "Market"
    assert BASE_TAXONOMY.resolve("SCHEME") == "Scheme"


def test_taxonomy_resolve_returns_none_for_unknown_category():
    assert BASE_TAXONOMY.resolve("Bullion") is None


def test_taxonomy_contains():
    assert "Crop" in BASE_TAXONOMY
    assert "crop" in BASE_TAXONOMY
    assert "Bullion" not in BASE_TAXONOMY
    assert 42 not in BASE_TAXONOMY  # non-str is simply not a member


def test_intent_defaults_to_empty_asks():
    intent = Intent(confidence=0.0)
    assert intent.asks == ()


def test_intent_confidence_must_be_within_unit_interval():
    with pytest.raises(ValidationError):
        Intent(confidence=1.1)
    with pytest.raises(ValidationError):
        Intent(confidence=-0.1)


def test_ask_is_frozen():
    ask = Ask(subject="potato", category="Crop", action_type=ActionType.ADVISORY)
    with pytest.raises(ValidationError):
        ask.subject = "wheat"


def test_action_type_values():
    assert ActionType.ADVISORY == "advisory"
    assert ActionType.LOOKUP == "lookup"
    assert ActionType.ACT == "act"


def test_intent_is_frozen():
    intent = Intent(confidence=0.5)
    with pytest.raises(ValidationError):
        intent.confidence = 0.9


def test_taxonomy_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Taxonomy(categories=("Crop",), extra="nope")
