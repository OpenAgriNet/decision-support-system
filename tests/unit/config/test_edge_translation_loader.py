"""Tier 1 — loading the edge translation config, and the file that ships."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.edge_translation_loader import load_edge_translation_config
from dss.core.shared.language import ENGLISH, LanguageTag

_SHIPPED = Path(__file__).resolve().parents[3] / "configs" / "edge-translation.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "edge-translation.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_no_path_means_no_languages() -> None:
    assert load_edge_translation_config(None) == {}


def test_shipped_file_binds_gujarati_to_english() -> None:
    languages = load_edge_translation_config(_SHIPPED)

    assert languages["gu"].to == ENGLISH
    assert languages["gu"].adapter == "stub"


def test_to_can_be_a_language_other_than_english(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  bhb: {to: mr, adapter: stub}\n")

    assert load_edge_translation_config(path)["bhb"].to == LanguageTag("mr")


def test_to_its_own_language_stops_the_boot(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  gu: {to: gu-IN, adapter: stub}\n")

    with pytest.raises(ValueError, match="gu -> gu-IN"):
        load_edge_translation_config(path)


def test_chained_route_stops_the_boot(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "languages:\n  bhb: {to: gu, adapter: stub}\n  gu: {to: en, adapter: stub}\n",
    )

    with pytest.raises(ValueError, match="bhb -> gu"):
        load_edge_translation_config(path)


def test_unknown_adapter_stops_the_boot(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  gu: {to: en, adapter: http}\n")

    with pytest.raises(ValueError, match=r"languages\.gu\.adapter"):
        load_edge_translation_config(path)


def test_region_key_stops_the_boot(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  gu-IN: {to: en, adapter: stub}\n")

    with pytest.raises(ValueError, match="gu-IN: a key must"):
        load_edge_translation_config(path)


def test_uppercase_key_stops_the_boot(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  GU: {to: en, adapter: stub}\n")

    with pytest.raises(ValueError, match="GU: a key must"):
        load_edge_translation_config(path)


def test_invalid_key_stops_the_boot(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  gujarati: {to: en, adapter: stub}\n")

    with pytest.raises(ValueError, match="gujarati: a key must"):
        load_edge_translation_config(path)


def test_missing_file_stops_the_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_edge_translation_config(tmp_path / "missing.yaml")


def test_misspelt_key_stops_the_boot(tmp_path: Path) -> None:
    path = write(tmp_path, "languages:\n  gu: {to: en, adaptor: stub}\n")

    with pytest.raises(ValueError, match=r"languages\.gu\.adaptor"):
        load_edge_translation_config(path)
