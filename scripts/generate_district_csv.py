"""Generate `src/dss/config/districts.csv` from an LGD area-lookup snapshot.

Not tested, by decision: this runs on a developer's machine when LGD publishes a
new snapshot, and its output — the checked-in CSV — is the reviewable artifact.
The adapter that reads that CSV on every boot is what carries tests.

The upstream snapshot is ~7MB and 25k rows: postal codes, blocks, districts,
states, country. Only districts are kept, for two reasons. The postal-code rows
in this snapshot carry their district's centroid verbatim — all 126 "Pune" rows
share one coordinate — so indexing them would add rows and no precision. Blocks
*do* have their own centroids, but they take name ambiguity from 3 names to 226,
and a wrong district is a ~1000km error in the discover call's spatial filter.

The snapshot names a district's state by LGD `parent_code` (27), while
`Location.region` speaks ISO 3166-2 ("IN-MH"). `same_as` is empty throughout, so
the join goes through the state *name* via the snapshot's own ISO-3166-2 rows.
That resolves all 784 districts, so a district that cannot find its region is
treated as a defect worth failing on rather than a row to drop silently.

Run when LGD publishes a new snapshot:

    uv run python scripts/generate_district_csv.py \
        --source <network-adapter>/tools/area-lookups/data/areas/<snapshot>/areas.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "src" / "dss" / "config" / "districts.csv"

# The generated header. Deliberately narrow: the adapter that reads this file
# should not have to know about bounding boxes, geometry provenance, or census
# codes. Widening it is a decision for whoever needs the extra column.
FIELDNAMES: tuple[str, ...] = (
    "area_code",
    "area_name",
    "region",
    "latitude",
    "longitude",
)


def _normalise(name: str) -> str:
    return " ".join(name.split()).lower()


def _read_rows(source: Path) -> list[dict[str, str]]:
    with source.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _iso_by_state_name(rows: list[dict[str, str]]) -> dict[str, str]:
    """ISO 3166-2 code per state name, e.g. `{"maharashtra": "IN-MH"}`."""

    return {
        _normalise(row["area_name"]): row["area_code"]
        for row in rows
        if row["code_scheme"] == "ISO-3166-2"
    }


def _state_name_by_lgd_code(rows: list[dict[str, str]]) -> dict[str, str]:
    """State name per LGD state code, e.g. `{"27": "Maharashtra"}`."""

    return {
        row["area_code"]: row["area_name"]
        for row in rows
        if row["code_scheme"] == "LGD" and row["area_level"] == "State"
    }


def districts(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The district rows, each carrying an ISO 3166-2 region.

    Raises if a district's state will not resolve — every one of the 784 in the
    2026-09-03 snapshot does, so a miss means the snapshot's shape changed and
    the region column would be quietly wrong.
    """

    iso_by_name = _iso_by_state_name(rows)
    state_by_code = _state_name_by_lgd_code(rows)

    out: list[dict[str, str]] = []
    unresolved: list[str] = []
    for row in rows:
        if row["code_scheme"] != "LGD" or row["area_level"] != "District":
            continue
        state_name = state_by_code.get(row["parent_code"])
        region = iso_by_name.get(_normalise(state_name)) if state_name else None
        if region is None:
            unresolved.append(f"{row['area_name']} (parent_code={row['parent_code']})")
            continue
        out.append(
            {
                "area_code": row["area_code"],
                "area_name": row["area_name"],
                "region": region,
                "latitude": row["latitude"],
                "longitude": row["longitude"],
            }
        )

    if unresolved:
        raise ValueError(
            f"{len(unresolved)} district(s) could not be mapped to an ISO 3166-2 "
            f"region: {', '.join(unresolved[:5])}"
        )
    return out


def write(rows: list[dict[str, str]], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as handle:
        # `\n`, not csv's default `\r\n`: the result is checked in, and CRLF
        # would fight git's line-ending handling on every regeneration.
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["region"], r["area_name"])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True, help="path to the LGD areas.csv snapshot"
    )
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"no such snapshot: {args.source}", file=sys.stderr)
        return 1

    rows = districts(_read_rows(args.source))
    write(rows, args.dest)
    print(f"wrote {len(rows)} districts to {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
