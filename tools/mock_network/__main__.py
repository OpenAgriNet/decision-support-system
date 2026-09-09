"""Run the mock network as a process.

    uv run python -m tools.mock_network --port 8078

Then point the DSS at it:

    DSS_DISCOVERY_BASE_URL=http://127.0.0.1:8078 \
    DSS_INVOCATION_BASE_URL=http://127.0.0.1:8078 \
    uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077

A separate process on purpose: real sockets, its own log, and `/docs` to poke
the two routes by hand. The same app object also mounts in-process for tests
via `ASGITransport`, so there is one body of mock behaviour rather than two
that can disagree.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from dss.config.schema_pack_fetch import DEFAULT_PACK_DIR
from tools.mock_network.app import build_mock_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="A stand-in for the OAN network.")
    parser.add_argument("--port", type=int, default=8078)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--pack-dir",
        type=Path,
        default=DEFAULT_PACK_DIR,
        help=(
            "the schema packs to read, the same ones the DSS reads "
            f"(default: {DEFAULT_PACK_DIR})"
        ),
    )
    args = parser.parse_args(argv)

    uvicorn.run(build_mock_app(pack_dir=args.pack_dir), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
