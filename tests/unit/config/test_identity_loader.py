"""Tier 1 — the identity loader and the shipped default identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.identity_loader import load_identity


def test_default_identity_loads_and_validates() -> None:
    identity = load_identity()
    assert identity.name
    assert identity.persona
    assert identity.boundaries


def test_set_but_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_identity(tmp_path / "does-not-exist.yaml")
