"""Download the network-specs schema packs the DSS routes against.

    uv run python scripts/fetch_schema_packs.py

The packs live in a separate repo and nothing clones them, so a fresh checkout
has none — and with the network enabled but no packs, the DSS refuses to boot
rather than answer every turn `no_match`.

Not packaged — `pyproject.toml` ships only `src/dss`. The logic lives in
`dss.config.schema_pack_fetch` so the app can call it at startup without
importing from `scripts/`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dss.config.schema_pack_fetch import (
    DEFAULT_PACK_DIR,
    SchemaPackFetchFailedError,
    fetch_packs,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Download the network-specs schema packs the DSS routes against."
    )
    parser.add_argument(
        "--ref",
        help=(
            "read this network-specs branch's preview instead of the published"
            " packs (default: the published packs)"
        ),
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_PACK_DIR,
        help=f"where to write them (default: {DEFAULT_PACK_DIR})",
    )
    args = parser.parse_args(argv)

    try:
        fetched = fetch_packs(ref=args.ref, dest=args.dest)
    except SchemaPackFetchFailedError as failure:
        # The message says what to check, so print it rather than a
        # traceback — the cause is a wrong ref or no network, not a crash.
        print(f"error: {failure}", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"wrote {len(fetched)} packs to {args.dest}: {', '.join(fetched)}")


if __name__ == "__main__":
    main()
