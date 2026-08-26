"""Tier 1 — the policy loader and the shipped default pack."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.policy_loader import load_policy_pack
from dss.core.policy.models import (
    Checkpoint,
    LlmPolicy,
    WordCheckPolicy,
)


def test_default_pack_loads_and_validates() -> None:
    pack = load_policy_pack()
    ids = {p.id for p in pack.policies}
    assert ids == {"profanity-filter", "delete-command"}


def test_default_pack_has_the_two_expected_shapes() -> None:
    pack = load_policy_pack()
    by_id = {p.id: p for p in pack.policies}
    assert isinstance(by_id["profanity-filter"], WordCheckPolicy)
    assert isinstance(by_id["delete-command"], LlmPolicy)
    assert by_id["profanity-filter"].words == ["shit"]


def test_all_default_policies_are_moderation_checkpoint() -> None:
    pack = load_policy_pack()
    assert pack.for_checkpoint(Checkpoint.MODERATION) == pack.policies


def test_set_but_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_policy_pack(tmp_path / "does-not-exist.yaml")


def test_adopter_pack_loads_from_path(tmp_path: Path) -> None:
    custom = tmp_path / "policies.yaml"
    custom.write_text(
        "version: 1\n"
        "policies:\n"
        "  - id: acme/profanity\n"
        "    evaluation: deterministic\n"
        "    description: strip\n"
        "    words: [damn]\n"
        "    warning: cleaned\n",
        encoding="utf-8",
    )
    pack = load_policy_pack(custom)
    assert [p.id for p in pack.policies] == ["acme/profanity"]
