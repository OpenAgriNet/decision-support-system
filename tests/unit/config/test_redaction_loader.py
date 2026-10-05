"""Tier 1 — loading the redaction rules file, and the example that ships."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dss.config.redaction_loader import EXAMPLE_RULES, load_redaction_config

RULES = """
entities:
  phone: keep
identifiers:
  - type: regex
    rules: [{entity: phone, kind: pattern, pattern: '\\d{10}'}]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_off_means_no_rules_even_with_a_path(tmp_path: Path) -> None:
    assert load_redaction_config(enabled=False, path=None) is None
    assert load_redaction_config(enabled=False, path=tmp_path / "x.yaml") is None


def test_on_without_a_path_refuses_to_boot() -> None:
    with pytest.raises(ValueError, match="DSS_REDACTION_CONFIG_PATH"):
        load_redaction_config(enabled=True, path=None)


def test_on_with_a_missing_file_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_redaction_config(enabled=True, path=tmp_path / "missing.yaml")


def test_a_file_loads_into_a_policy_and_identifier_entries(tmp_path: Path) -> None:
    config = load_redaction_config(enabled=True, path=write(tmp_path, RULES))
    assert config is not None
    assert config.policy.keeps("phone")
    assert [entry["type"] for entry in config.identifiers] == ["regex"]


def test_an_empty_file_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_redaction_config(enabled=True, path=write(tmp_path, ""))


def test_an_identifier_without_a_type_refuses_to_boot(tmp_path: Path) -> None:
    text = "entities: {phone: keep}\nidentifiers: [{rules: []}]\n"
    with pytest.raises(ValidationError, match="no 'type'"):
        load_redaction_config(enabled=True, path=write(tmp_path, text))


def test_a_bad_keep_or_destroy_refuses_to_boot(tmp_path: Path) -> None:
    text = RULES.replace("phone: keep", "phone: hide")
    with pytest.raises(ValidationError):
        load_redaction_config(enabled=True, path=write(tmp_path, text))


def test_the_example_file_destroys_aadhaar_and_card_only() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    destroyed = {e for e in config.entities if not config.policy.keeps(e)}
    assert destroyed == {"aadhaar", "card"}
    assert [entry["type"] for entry in config.identifiers] == ["regex", "spacy"]
