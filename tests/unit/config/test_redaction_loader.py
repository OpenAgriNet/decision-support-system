"""Tier 1 — loading the redaction rules, and the example file that ships."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dss.config.redaction_loader import EXAMPLE_RULES, load_redaction_config
from dss.core.redaction.service import redact


def test_off_means_no_rules_even_with_a_path(tmp_path: Path) -> None:
    assert load_redaction_config(enabled=False, path=None) is None
    assert load_redaction_config(enabled=False, path=tmp_path / "x.yaml") is None


def test_on_without_a_path_refuses_to_boot() -> None:
    with pytest.raises(ValueError, match="DSS_REDACTION_CONFIG_PATH"):
        load_redaction_config(enabled=True, path=None)


def test_on_with_a_missing_file_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_redaction_config(enabled=True, path=tmp_path / "missing.yaml")


def test_a_bad_rule_refuses_to_boot_and_names_the_rule(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "rules:\n"
        "  - entity: phone\n"
        "    kind: pattern\n"
        "    pattern: '[6-9'\n"
        "    value: keep\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="rule 'phone'"):
        load_redaction_config(enabled=True, path=rules)


def test_an_empty_file_refuses_to_boot(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_redaction_config(enabled=True, path=rules)


def test_the_example_file_loads() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    assert [r.entity for r in config.rules] == [
        "aadhaar",
        "card",
        "gstin",
        "pan",
        "ifsc",
        "phone",
        "email",
        "person",
    ]


def test_the_example_file_destroys_aadhaar_and_card_only() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    destroyed = {r.entity for r in config.rules if r.value == "destroy"}
    assert destroyed == {"aadhaar", "card"}


def test_the_example_file_redacts_the_cards_examples() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    result = redact(
        [
            "mera aadhaar 2345 6789 0124 hai, gehu ka rate kya hai?",
            "mera number 98765 43210 hai, meri application ka status?",
            "champa ka rate kya hai?",
        ],
        config,
    )
    assert result.texts == (
        "mera aadhaar «aadhaar_1» hai, gehu ka rate kya hai?",
        "mera number «phone_1» hai, meri application ka status?",
        "champa ka rate kya hai?",
    )
