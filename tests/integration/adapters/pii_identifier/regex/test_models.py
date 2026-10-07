"""Tier 2 — a bad regex rule fails when its settings are built, and names itself."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.adapters.pii_identifier.regex.models import (
    DeclaringPhraseRule,
    PatternRule,
    RegexSettings,
)


def test_a_pattern_that_does_not_compile_names_its_rule() -> None:
    with pytest.raises(ValidationError, match="rule 'phone'.*does not compile"):
        PatternRule(entity="phone", pattern="[6-9")


def test_an_unknown_validator_names_its_rule() -> None:
    with pytest.raises(ValidationError, match="rule 'aadhaar'.*unknown validator"):
        PatternRule(entity="aadhaar", pattern=r"\d{12}", validator="nope")


def test_entity_names_must_fit_in_a_tag() -> None:
    with pytest.raises(ValidationError):
        PatternRule(entity="Phone Number", pattern=r"\d")


def test_a_declaring_rule_needs_a_phrase() -> None:
    with pytest.raises(ValidationError):
        DeclaringPhraseRule(entity="person", phrases=[])


def test_a_config_needs_at_least_one_rule() -> None:
    with pytest.raises(ValidationError):
        RegexSettings(rules=[])


def test_a_rule_does_not_say_keep_or_destroy() -> None:
    # That is the entities policy's job, for every identifier.
    with pytest.raises(ValidationError):
        PatternRule(entity="phone", pattern=r"\d", value="keep")


def test_rules_parse_from_plain_data() -> None:
    config = RegexSettings.model_validate(
        {
            "rules": [
                {"entity": "phone", "kind": "pattern", "pattern": r"\d{10}"},
                {
                    "entity": "person",
                    "kind": "declaring_phrase",
                    "phrases": ["my name is"],
                },
            ]
        }
    )
    assert [type(r) for r in config.rules] == [PatternRule, DeclaringPhraseRule]


def test_a_grouping_with_a_zero_or_negative_size_is_refused() -> None:
    with pytest.raises(ValidationError, match="rule 'card'"):
        PatternRule(entity="card", pattern=r"\d{16}", groupings=[[4, 0, 4]])


def test_groupings_default_to_any() -> None:
    assert PatternRule(entity="phone", pattern=r"\d{10}").groupings == []
