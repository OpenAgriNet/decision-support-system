"""Tier 2 — the CSV scheme catalog against a fixture file on disk.

No network. Asserts the adapter's contract: the rows it hands the intent
model, and that a broken file fails loudly and names what is wrong.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dss.adapters.scheme_catalog.csv_file import (
    MalformedSchemeCatalogError,
    load_scheme_catalog,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "schemes.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_carries_the_scheme_code_through_verbatim() -> None:
    """The code is what a scheme pack selects its form by, so case is kept."""

    schemes = load_scheme_catalog(FIXTURES / "schemes.csv").schemes()

    assert schemes[-1].scheme.code == "PMFBY"
    assert schemes[-1].scheme.name == "Pradhan Mantri Fasal Bima Yojana"


def test_lists_every_scheme_with_its_other_names_once() -> None:
    """What the intent model is shown: file order, and neither the official
    name nor a mere case variant of it repeated as an alias."""

    schemes = load_scheme_catalog(FIXTURES / "schemes.csv").schemes()

    assert [entry.scheme.code for entry in schemes] == [
        "makhana",
        "pkvy",
        "mif",
        "pm-ddky",
        "PMFBY",
    ]
    pkvy = schemes[1]
    assert pkvy.aliases == ("PKVY", "organic farming scheme")


def test_punctuation_variants_of_one_alias_are_shown_once() -> None:
    schemes = load_scheme_catalog(FIXTURES / "schemes.csv").schemes()

    pm_ddky = schemes[3]
    assert pm_ddky.aliases == ("PM-DDKY", "dhan-dhaanya", "dhan dhaanya krishi yojana")


def test_unset_path_yields_an_empty_catalog_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No catalog ships in the image, so an unmounted one is a real
    deployment state — inert, but an operator who forgot the mount should
    find out from the logs rather than from bad answers."""

    with caplog.at_level(logging.WARNING):
        catalog = load_scheme_catalog(None)

    assert catalog.schemes() == ()
    assert "DSS_SCHEMES_CONFIG_PATH" in caplog.text


def test_configured_but_missing_path_raises(tmp_path: Path) -> None:
    """Never boot on a different configuration than the one asked for."""

    with pytest.raises(FileNotFoundError):
        load_scheme_catalog(tmp_path / "absent.csv")


def test_missing_column_names_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "scheme_code,scheme_name\nmif,Micro Irrigation Fund\n")

    with pytest.raises(MalformedSchemeCatalogError) as exc:
        load_scheme_catalog(path)

    assert "scheme_aliases" in str(exc.value)
    assert path.name in str(exc.value)


def test_blank_scheme_name_names_the_row(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "scheme_code,scheme_name,scheme_aliases\nmif,Micro Irrigation Fund,MIF\nx,,y\n",
    )

    with pytest.raises(MalformedSchemeCatalogError) as exc:
        load_scheme_catalog(path)

    # Row 3, counting the header — what the editor's cursor shows.
    assert "row 3" in str(exc.value)


def test_an_alias_shared_by_two_schemes_raises(tmp_path: Path) -> None:
    """Two schemes claiming one alias leave no right answer for which the
    farmer meant, so the catalog is refused rather than shown to the model."""

    path = _write(
        tmp_path,
        "scheme_code,scheme_name,scheme_aliases\n"
        "a,Scheme A,shared alias\n"
        "b,Scheme B,shared alias\n",
    )

    with pytest.raises(MalformedSchemeCatalogError) as exc:
        load_scheme_catalog(path)

    assert "shared alias" in str(exc.value)


def test_empty_and_whitespace_aliases_are_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "scheme_code,scheme_name,scheme_aliases\nmif,Micro Irrigation Fund,MIF||  |\n",
    )

    (entry,) = load_scheme_catalog(path).schemes()

    assert entry.aliases == ("MIF",)
