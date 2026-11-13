"""Tier 1 — the clarification-text loader and the shipped default text."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.clarification_text_loader import load_clarification_text


def test_default_clarification_text_loads_and_validates() -> None:
    text = load_clarification_text()
    assert text.more_places_hint
    assert text.no_provider
    assert text.unknown_form


def test_set_but_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_clarification_text(tmp_path / "does-not-exist.yaml")
