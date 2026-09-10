"""Contract for the CSV-backed area lookup.

Tier 2: a real file read is the adapter's whole job, so these tests write a
fixture CSV rather than mocking the parse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.adapters.area_lookup.csv_lookup import CsvAreaLookup, DistrictCsvUnusable
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaMatch

HEADER = "area_code,area_name,region,latitude,longitude\n"


def _write(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "districts.csv"
    path.write_text(HEADER + "".join(f"{row}\n" for row in rows), encoding="utf-8")
    return path


def test_resolves_a_district_name_to_its_coordinates(tmp_path: Path) -> None:
    """`coordinates` is `[lon, lat]` — GeoJSON order, longitude first.

    Asserted explicitly because a swap here is invisible: it would place Pune in
    the Arabian Sea and still look like a plausible pair of numbers.
    """

    path = _write(tmp_path, "490,Pune,IN-MH,18.571118,74.067998")

    lookup = CsvAreaLookup.load(path)

    assert lookup.resolve("Pune") == [
        AreaMatch(
            name="Pune",
            region="IN-MH",
            geometry=Geometry(coordinates=[74.067998, 18.571118]),
        )
    ]


def test_matches_the_name_regardless_of_case_and_padding(tmp_path: Path) -> None:
    """The farmer's words reach this port via an LLM, so casing and stray
    whitespace vary. The name comes back in the index's spelling, not the
    caller's — that string is what a follow-up question would show the farmer.
    """

    path = _write(tmp_path, "490,Pune,IN-MH,18.571118,74.067998")

    lookup = CsvAreaLookup.load(path)

    for spelling in ("Pune", "pune", "PUNE", "  Pune  "):
        assert [match.name for match in lookup.resolve(spelling)] == ["Pune"], spelling


# The two real Bilaspurs, verbatim from the generated CSV. One of exactly three
# district names in India that collide (also Hamirpur, Pratapgarh).
_BILASPUR_CT = "375,Bilaspur,IN-CT,22.179960,82.115906"
_BILASPUR_HP = "15,Bilaspur,IN-HP,31.370997,76.670218"


def test_reports_every_match_when_a_name_is_ambiguous(tmp_path: Path) -> None:
    """Picking one silently would be a ~1000km error in the spatial filter, so
    both come back and the caller asks which."""

    path = _write(tmp_path, _BILASPUR_CT, _BILASPUR_HP)

    lookup = CsvAreaLookup.load(path)

    assert {match.region for match in lookup.resolve("Bilaspur")} == {
        "IN-CT",
        "IN-HP",
    }


def test_region_narrows_an_ambiguous_name_to_one(tmp_path: Path) -> None:
    """`Location.region` is often absent, but when the turn carries it the
    collision resolves without asking the farmer anything."""

    path = _write(tmp_path, _BILASPUR_CT, _BILASPUR_HP)

    lookup = CsvAreaLookup.load(path)

    assert [match.region for match in lookup.resolve("Bilaspur", region="IN-HP")] == [
        "IN-HP"
    ]


def test_returns_no_match_for_a_name_the_index_does_not_carry(tmp_path: Path) -> None:
    """A village or city name resolves to nothing, because the index holds only
    districts. Empty is a real answer, not a failure — it is what tells the
    caller to ask the farmer which district they are in.
    """

    path = _write(tmp_path, "490,Pune,IN-MH,18.571118,74.067998")

    lookup = CsvAreaLookup.load(path)

    assert lookup.resolve("Shirur") == []


def test_a_missing_file_refuses_to_load_and_names_the_path(tmp_path: Path) -> None:
    """The CSV is checked in, so a missing one is a broken build or a bad
    DSS_DISTRICT_CSV_PATH. Failing at load makes the app refuse to boot, rather
    than serving turns that have quietly lost every spatial filter.
    """

    missing = tmp_path / "districts.csv"

    with pytest.raises(DistrictCsvUnusable) as raised:
        CsvAreaLookup.load(missing)

    assert str(missing) in str(raised.value)


def test_a_file_with_no_districts_refuses_to_load(tmp_path: Path) -> None:
    """Header-only reads as a successful parse but resolves nothing, which is
    the same silent failure as a missing file."""

    path = tmp_path / "districts.csv"
    path.write_text(HEADER, encoding="utf-8")

    with pytest.raises(DistrictCsvUnusable):
        CsvAreaLookup.load(path)
