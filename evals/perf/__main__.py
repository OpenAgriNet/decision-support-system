"""Run the speed benchmark against a running DSS.

    uv run python -m evals.perf --repeats 3 --warmup 2

Needs the local stack up (`scripts/run-local.sh`): the DSS, the mock network
and Langfuse. Reads LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY from the
environment. Prints the report and writes it as JSON under `var/evals/perf/`.

Only wiring lives here: each piece it calls is tested on its own, and this
file is checked by a real run.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio
import httpx

from evals.perf.questions import load_questions
from evals.perf.report import figures, render_text, write_json
from evals.perf.runner import RunOptions, run
from evals.perf.traces import fetch_trace
from evals.perf.turn import run_turn

_HERE = Path(__file__).parent
_REPO = _HERE.parents[1]


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (public and secret):
        sys.exit("export LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY first")
    anyio.run(partial(_run, args, (public, secret)))


async def _run(args: argparse.Namespace, keys: tuple[str, str]) -> None:
    questions = load_questions(args.questions, lang=args.lang)
    since = datetime.now(UTC)
    async with (
        httpx.AsyncClient() as dss,
        httpx.AsyncClient(base_url=args.mock_url) as mock,
        httpx.AsyncClient(base_url=args.langfuse_url, auth=keys, timeout=30) as lf,
    ):

        async def turn(question, session_id):
            return await run_turn(
                dss,
                args.base_url,
                question,
                session_id=session_id,
                transaction_id=session_id,
            )

        async def misses():
            body = (await mock.get("/_bench/misses")).json()
            return set(body["transactionIds"])

        async def trace(session_id):
            return await fetch_trace(lf, session_id, since=since)

        result = await run(
            questions,
            RunOptions(
                warmup=args.warmup, repeats=args.repeats, max_turns=args.max_turns
            ),
            turn=turn,
            misses=misses,
            trace=trace,
        )

    report = figures(result, commit=_commit(), machine=_machine())
    print(render_text(report))
    print(f"\nwritten: {write_json(report, result, args.out)}")


def _commit() -> str:
    """The commit the DSS was run from, marked when the tree has changes, or
    `unknown` with no git (a copy of the code without `.git`)."""

    def git(*command: str) -> str:
        try:
            done = subprocess.run(
                ["git", *command], cwd=_REPO, capture_output=True, text=True
            )
        except FileNotFoundError:
            return ""
        return done.stdout.strip()

    commit = git("rev-parse", "HEAD")
    if not commit:
        return "unknown"
    return commit + ("-dirty" if git("status", "--porcelain") else "")


def _machine() -> dict:
    return {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Time the DSS on fixed questions.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8077")
    parser.add_argument("--mock-url", default="http://127.0.0.1:8078")
    parser.add_argument("--langfuse-url", default="http://localhost:3000")
    parser.add_argument("--questions", type=Path, default=_HERE / "questions.toml")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="stop after this many turns, warm-up included; caps what a run costs",
    )
    parser.add_argument("--out", type=Path, default=_REPO / "var" / "evals" / "perf")
    return parser


if __name__ == "__main__":
    main()
