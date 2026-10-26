"""Download the network-specs schema packs the DSS routes against.

    uv run python scripts/fetch_schema_packs.py --ref schema-packs-v0.1
    uv run python scripts/fetch_schema_packs.py --api --ref api-schema-packs-v0.1

The second line fetches the API schema packs the DSS builds forms from.

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
    API_PACK_SET,
    CATALOG_PACK_SET,
    DEFAULT_REF,
    SchemaPackFetchFailedError,
    fetch_packs,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download the network-specs schema packs: catalog, or API with --api."
        )
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
        "--api",
        action="store_true",
        help=(
            "fetch the API schema packs (forms) instead of the catalog packs "
            f"(published on {API_PACK_SET.published_ref})"
        ),
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help=(
            f"where to write them (default: {CATALOG_PACK_SET.default_dir}, "
            f"or {API_PACK_SET.default_dir} with --api)"
        ),
    )
    args = parser.parse_args(argv)
    pack_set = API_PACK_SET if args.api else CATALOG_PACK_SET
    dest = args.dest or pack_set.default_dir

    try:
        fetched = fetch_packs(ref=args.ref, dest=dest, pack_set=pack_set)
    except SchemaPackFetchFailedError as failure:
        # The message names the ref and where the packs are, so print it
        # rather than a traceback — the cause is a wrong ref, not a crash.
        print(f"error: {failure}", file=sys.stderr)
        raise SystemExit(1) from None

    print(f"wrote {len(fetched)} packs to {dest}: {', '.join(fetched)}")


if __name__ == "__main__":
    main()
