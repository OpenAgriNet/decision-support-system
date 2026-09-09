"""Download the network-specs schema packs the DSS routes against.

    uv run python scripts/fetch_schema_packs.py --ref schema-packs-v0.1

The packs live in a separate repo and nothing clones them, so a fresh checkout
has none — and with the network enabled but no packs, the DSS refuses to boot
rather than answer every turn `no_match`.

Note the default ref carries only a README. That is deliberate: `main` is what
the packs will land on, and until they do the fetch fails loudly saying which
ref actually has them.

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
    DEFAULT_REF,
    SchemaPackFetchFailed,
    fetch_packs,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Download the network-specs schema packs the DSS routes against."
    )
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help=(
            "the network-specs branch or tag to read "
            f"(default: {DEFAULT_REF}, which carries no packs yet)"
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
    except SchemaPackFetchFailed as failure:
        # The message names the ref and where the packs are, so print it
        # rather than a traceback — the cause is a wrong ref, not a crash.
        print(f"error: {failure}", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"wrote {len(fetched)} packs to {args.dest}: {', '.join(fetched)}")


if __name__ == "__main__":
    main()
