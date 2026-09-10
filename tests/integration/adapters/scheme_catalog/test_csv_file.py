"""Tier 2 — the CSV scheme catalog against a fixture file on disk.

No network. Asserts the adapter's contract: the normalized alias index it
builds, and that a broken file fails loudly and names what is wrong.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dss.adapters.scheme_catalog.csv_file import (
    MalformedSchemeCatalog,
    load_scheme_catalog,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "schemes.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_indexes_every_alias_normalized() -> None:
    aliases = load_scheme_catalog(FIXTURES / "schemes.csv").aliases()

    assert aliases["makhana scheme"].code == "makhana"
    assert aliases["horticulture makhana"].code == "makhana"
    # Punctuation-insensitive: three spellings of one acronym, one key.
    assert aliases["pm ddky"].code == "pm-ddky"
    assert aliases["dhan dhaanya"].code == "pm-ddky"


def test_indexes_the_scheme_name_itself() -> None:
    """The official name is matchable even when nobody listed it as an alias."""

    aliases = load_scheme_catalog(FIXTURES / "schemes.csv").aliases()

    assert aliases["micro irrigation fund"].name == "Micro Irrigation Fund"


def test_carries_the_scheme_code_through() -> None:
    aliases = load_scheme_catalog(FIXTURES / "schemes.csv").aliases()

    assert aliases["pkvy"] == aliases["organic farming scheme"]
    assert aliases["pkvy"].code == "pkvy"


def test_no_bare_commodity_alias_in_the_fixture() -> None:
    """The correctness of the category override (#36) rests on the catalog
    holding no bare commodity words. The source document shipped `makhana`
    and `foxnut` as aliases; both are wrong and stay out.
    """

    aliases = load_scheme_catalog(FIXTURES / "schemes.csv").aliases()

    assert "makhana" not in aliases
    assert "foxnut" not in aliases


def test_unset_path_yields_an_empty_catalog_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No catalog ships in the image, so an unmounted one is a real
    deployment state — inert, but an operator who forgot the mount should
    find out from the logs rather than from bad answers."""

    with caplog.at_level(logging.WARNING):
        catalog = load_scheme_catalog(None)

    assert catalog.aliases() == {}
    assert "DSS_SCHEMES_CONFIG_PATH" in caplog.text


def test_configured_but_missing_path_raises(tmp_path: Path) -> None:
    """Never boot on a different configuration than the one asked for."""

    with pytest.raises(FileNotFoundError):
        load_scheme_catalog(tmp_path / "absent.csv")


def test_missing_column_names_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "scheme_code,scheme_name\nmif,Micro Irrigation Fund\n")

    with pytest.raises(MalformedSchemeCatalog) as exc:
        load_scheme_catalog(path)

    assert "scheme_aliases" in str(exc.value)
    assert path.name in str(exc.value)


def test_blank_scheme_name_names_the_row(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "scheme_code,scheme_name,scheme_aliases\nmif,Micro Irrigation Fund,MIF\nx,,y\n",
    )

    with pytest.raises(MalformedSchemeCatalog) as exc:
        load_scheme_catalog(path)

    # Row 3, counting the header — what the editor's cursor shows.
    assert "row 3" in str(exc.value)


def test_an_alias_shared_by_two_schemes_raises(tmp_path: Path) -> None:
    """Not the same judgement call as a commodity word: `longest span wins`
    cannot choose between two schemes claiming one alias, so the catalog is
    ambiguous and unusable rather than merely questionable."""

    path = _write(
        tmp_path,
        "scheme_code,scheme_name,scheme_aliases\n"
        "a,Scheme A,shared alias\n"
        "b,Scheme B,shared alias\n",
    )

    with pytest.raises(MalformedSchemeCatalog) as exc:
        load_scheme_catalog(path)

    assert "shared alias" in str(exc.value)


def test_empty_and_whitespace_aliases_are_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "scheme_code,scheme_name,scheme_aliases\nmif,Micro Irrigation Fund,MIF||  |\n",
    )

    aliases = load_scheme_catalog(path).aliases()

    assert set(aliases) == {"mif", "micro irrigation fund"}
