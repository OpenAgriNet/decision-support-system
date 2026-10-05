"""Generate `src/dss/config/areas.csv` from an LGD area-lookup snapshot.

Not tested, by decision: this runs on a developer's machine when LGD publishes a
new snapshot, and its output — the checked-in CSV — is the reviewable artifact.
The adapter that reads that CSV on every boot is what carries tests.

The upstream snapshot is ~7MB and 25k rows: postal codes, blocks, districts,
states, country. Postal codes are never kept: this snapshot's rows carry their
district's centroid verbatim — all 126 "Pune" rows share one coordinate — so
indexing them would add rows and no precision.

Districts and blocks are both kept. Blocks take name ambiguity from 3 names to
226, and this snapshot's block rows carry an inherited district/state centroid
rather than their own point (`point_method` is `inherited:*` for every one) —
a real gap, not fixed here; it needs the snapshot rebuilt with
`join_geometry.py --source lgd`, which is a decision for whoever runs that
rebuild. Shipping them anyway still fixes name resolution: a farmer naming
their block is recognised, even though the point returned is the district's.

The snapshot names a district's state, and a block's district, by LGD
`parent_code`, while `Location.region` speaks ISO 3166-2 ("IN-MH"). `same_as`
is empty throughout, so the join goes through the state *name* via the
snapshot's own ISO-3166-2 rows. That resolves every district in the
2026-09-03 snapshot, so an area that cannot find its ancestor chain is
treated as a defect worth failing on rather than a row to drop silently.

`within` is the ancestor chain, coarsest first, no level words: a district's
is `("India", "<state>")`; a block's is `("India", "<state>", "<district>")`.
"India" is hardcoded — this snapshot covers India alone, and the country level
never varies within it.

Run when LGD publishes a new snapshot:

    uv run python scripts/generate_area_csv.py \
        --source <network-adapter>/tools/area-lookups/data/areas/<snapshot>/areas.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "src" / "dss" / "config" / "areas.csv"

# The generated header. Deliberately narrow: the adapter that reads this file
# should not have to know about bounding boxes, geometry provenance, or census
# codes. Widening it is a decision for whoever needs the extra column.
FIELDNAMES: tuple[str, ...] = (
    "area_code",
    "area_name",
    "region",
    "latitude",
    "longitude",
    # Other names a farmer may use for the area, `;`-separated
    # ("Bangalore;Bangalore City"). Hand-maintained in `areas.csv` — the LGD
    # snapshot carries none — so a regeneration reads the existing file and
    # carries them across rather than blanking the column.
    "aliases",
    # Ancestor chain, coarsest first, `;`-separated ("India;Maharashtra").
    "within",
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


def _district_name_by_lgd_code(rows: list[dict[str, str]]) -> dict[str, str]:
    """District name per LGD district code, e.g. `{"360": "Kendrapada"}`. A
    block's `parent_code` names one of these, the same way a district's own
    `parent_code` names a state."""

    return {
        row["area_code"]: row["area_name"]
        for row in rows
        if row["code_scheme"] == "LGD" and row["area_level"] == "District"
    }


def _district_parent_by_lgd_code(rows: list[dict[str, str]]) -> dict[str, str]:
    """A district's own state `parent_code`, e.g. `{"360": "21"}`. Blocks
    carry no state code directly — only their district's — so a block's
    `within` walks block → district (this) → district's state."""

    return {
        row["area_code"]: row["parent_code"]
        for row in rows
        if row["code_scheme"] == "LGD" and row["area_level"] == "District"
    }


def existing_aliases(dest: Path) -> dict[tuple[str, str], str]:
    """Aliases already in the generated file, keyed by `(area_name, region)`.

    The LGD snapshot has no alias data — the column is hand-maintained in
    `areas.csv` itself. Reading it back means a regeneration against a
    newer snapshot keeps them, instead of silently blanking the column.

    An area that disappears from the snapshot takes its aliases with it,
    which is the intended behaviour: an alias for an area that no longer
    exists cannot resolve to anything.
    """

    if not dest.is_file():
        return {}
    with dest.open(newline="", encoding="utf-8") as handle:
        return {
            (row["area_name"], row["region"]): row["aliases"]
            for row in csv.DictReader(handle)
            if row.get("aliases")
        }


def _district_row(
    row: dict[str, str],
    *,
    iso_by_name: dict[str, str],
    state_by_code: dict[str, str],
    aliases: dict[tuple[str, str], str],
) -> tuple[dict[str, str] | None, str | None]:
    """One district row, or `(None, <what could not resolve>)`."""

    state_name = state_by_code.get(row["parent_code"])
    region = iso_by_name.get(_normalise(state_name)) if state_name else None
    if region is None or state_name is None:
        return None, f"{row['area_name']} (parent_code={row['parent_code']})"
    return {
        "area_code": row["area_code"],
        "area_name": row["area_name"],
        "region": region,
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "aliases": aliases.get((row["area_name"], region), ""),
        "within": "India;" + state_name,
    }, None


def _block_row(
    row: dict[str, str],
    *,
    iso_by_name: dict[str, str],
    state_by_code: dict[str, str],
    district_by_code: dict[str, str],
    district_parent_by_code: dict[str, str],
    aliases: dict[tuple[str, str], str],
) -> tuple[dict[str, str] | None, str | None]:
    """One block row, or `(None, <what could not resolve>)`.

    A block's `parent_code` names its district; the district's own
    `parent_code` names its state — one level further than a district walks.
    """

    district_name = district_by_code.get(row["parent_code"])
    state_code = district_parent_by_code.get(row["parent_code"])
    state_name = state_by_code.get(state_code) if state_code else None
    region = iso_by_name.get(_normalise(state_name)) if state_name else None
    if region is None or state_name is None or district_name is None:
        return None, f"{row['area_name']} (parent_code={row['parent_code']})"
    return {
        "area_code": row["area_code"],
        "area_name": row["area_name"],
        "region": region,
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "aliases": aliases.get((row["area_name"], region), ""),
        "within": f"India;{state_name};{district_name}",
    }, None


def areas(
    rows: list[dict[str, str]], aliases: dict[tuple[str, str], str] | None = None
) -> list[dict[str, str]]:
    """The district and block rows, each carrying an ISO 3166-2 region and a
    coarsest-first `within` chain.

    Raises if an area's ancestor chain will not resolve — every district and
    block in the 2026-09-03 snapshot does, so a miss means the snapshot's
    shape changed and the region/within columns would be quietly wrong.
    """

    aliases = aliases or {}
    iso_by_name = _iso_by_state_name(rows)
    state_by_code = _state_name_by_lgd_code(rows)
    district_by_code = _district_name_by_lgd_code(rows)
    district_parent_by_code = _district_parent_by_lgd_code(rows)

    out: list[dict[str, str]] = []
    unresolved: list[str] = []
    for row in rows:
        if row["code_scheme"] != "LGD":
            continue
        if row["area_level"] == "District":
            built, failure = _district_row(
                row,
                iso_by_name=iso_by_name,
                state_by_code=state_by_code,
                aliases=aliases,
            )
        elif row["area_level"] == "Block":
            built, failure = _block_row(
                row,
                iso_by_name=iso_by_name,
                state_by_code=state_by_code,
                district_by_code=district_by_code,
                district_parent_by_code=district_parent_by_code,
                aliases=aliases,
            )
        else:
            continue
        if built is not None:
            out.append(built)
        else:
            assert failure is not None
            unresolved.append(failure)

    if unresolved:
        raise ValueError(
            f"{len(unresolved)} area(s) could not be mapped to an ISO 3166-2 "
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

    rows = areas(_read_rows(args.source), existing_aliases(args.dest))
    write(rows, args.dest)
    print(f"wrote {len(rows)} areas to {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
