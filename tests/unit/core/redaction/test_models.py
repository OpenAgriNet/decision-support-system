"""Tier 1 — a bad rule fails when the config is built, and names itself."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.redaction.models import (
    DeclaringPhraseRule,
    PatternRule,
    RedactionConfig,
    ValueHandling,
)


def test_a_pattern_that_does_not_compile_names_its_rule() -> None:
    with pytest.raises(ValidationError, match="rule 'phone'.*does not compile"):
        PatternRule(entity="phone", pattern="[6-9", value=ValueHandling.KEEP)


def test_an_unknown_validator_names_its_rule() -> None:
    with pytest.raises(ValidationError, match="rule 'aadhaar'.*unknown validator"):
        PatternRule(
            entity="aadhaar",
            pattern=r"\d{12}",
            validator="nope",
            value=ValueHandling.DESTROY,
        )


def test_entity_names_must_fit_in_a_tag() -> None:
    with pytest.raises(ValidationError):
        PatternRule(entity="Phone Number", pattern=r"\d", value=ValueHandling.KEEP)


def test_a_declaring_rule_needs_a_phrase() -> None:
    with pytest.raises(ValidationError):
        DeclaringPhraseRule(entity="person", phrases=[], value=ValueHandling.KEEP)


def test_a_config_needs_at_least_one_rule() -> None:
    with pytest.raises(ValidationError):
        RedactionConfig(rules=[])


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PatternRule(entity="phone", pattern=r"\d", value=ValueHandling.KEEP, keep=True)


def test_rules_parse_from_plain_data() -> None:
    config = RedactionConfig.model_validate(
        {
            "rules": [
                {
                    "entity": "phone",
                    "kind": "pattern",
                    "pattern": r"\d{10}",
                    "value": "keep",
                },
                {
                    "entity": "person",
                    "kind": "declaring_phrase",
                    "phrases": ["my name is"],
                    "value": "keep",
                },
            ]
        }
    )
    assert [type(r) for r in config.rules] == [PatternRule, DeclaringPhraseRule]
