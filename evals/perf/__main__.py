"""Run the speed benchmark, or its load mode, against the DSS.

    uv run python -m evals.perf --repeats 3 --warmup 2
    uv run python -m evals.perf load --concurrency 1,2,4,8

Both need the mock network and Langfuse up (`scripts/run-local.sh`), and read
LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY from the environment.

- The speed run times the DSS already running on `--base-url`, one turn at a
  time.
- Load mode starts its own DSS in a container pinned to `--cpus` and
  `--memory`, so the figures mean "one pod", not "this laptop". It needs the
  model settings (`DSS_*_MODEL`, the model keys) exported in this shell.

Each writes its report as JSON under `var/evals/perf/`. Only wiring lives
here: each piece it calls is tested on its own, and this file is checked by a
real run.
"""

from __future__ import annotations

import argparse
import base64
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio
import httpx

from evals.perf import container
from evals.perf.load import run_load
from evals.perf.questions import load_questions
from evals.perf.report import (
    figures,
    load_figures,
    render_load_text,
    render_text,
    save_json,
    write_json,
)
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
    if args.mode == "load":
        _load(args, (public, secret))
    else:
        anyio.run(partial(_speed, args, (public, secret)))


async def _speed(args: argparse.Namespace, keys: tuple[str, str]) -> None:
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

        async def trace(session_id):
            return await fetch_trace(lf, session_id, since=since)

        result = await run(
            questions,
            RunOptions(
                warmup=args.warmup, repeats=args.repeats, max_turns=args.max_turns
            ),
            turn=turn,
            misses=partial(_misses, mock),
            trace=trace,
        )

    report = figures(result, commit=_commit(), machine=_machine())
    print(render_text(report))
    print(f"\nwritten: {write_json(report, result, args.out)}")


def _load(args: argparse.Namespace, keys: tuple[str, str]) -> None:
    if container.missing_model_key(os.environ):
        sys.exit(
            "export the model settings in this shell first: OPENAI_API_KEY (and "
            "OPENAI_BASE_URL) or AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT, "
            "plus any DSS_*_MODEL. The container gets only what is exported here."
        )
    runtime = container.runtime()
    if not args.no_build:
        container.build(runtime, args.image, _REPO)
    _trace_to_langfuse(keys)
    pass_env, set_env = container.container_env(
        os.environ, host=container.host_alias(runtime), mock_port=_port(args.mock_url)
    )
    started = container.start(
        runtime,
        args.image,
        cpus=args.cpus,
        memory=args.memory,
        port=args.port,
        pack_dir=_REPO / "var" / "schema-packs",
        pass_env=pass_env,
        set_env=set_env,
    )
    log = args.out / f"{datetime.now(UTC):%Y-%m-%dT%H-%M-%SZ}-load-container.log"
    try:
        limits = container.limits(runtime, started)
        steps = anyio.run(partial(_load_steps, args))
    finally:
        # Kept before stopping: `--rm` removes the container and its log.
        args.out.mkdir(parents=True, exist_ok=True)
        log.write_text(container.logs(runtime, started))
        container.stop(runtime, started)
        print(f"container log: {log}")

    report = load_figures(steps, limits=limits, machine=_machine(), commit=_commit())
    print(render_load_text(report))
    print(f"\nwritten: {save_json(report, args.out, kind='load')}")


async def _load_steps(args: argparse.Namespace):
    base_url = f"http://127.0.0.1:{args.port}"
    questions = load_questions(args.questions, lang=args.lang)
    async with (
        httpx.AsyncClient() as dss,
        httpx.AsyncClient(base_url=args.mock_url) as mock,
    ):
        await _wait_until_up(dss, base_url)

        async def turn(question, session_id):
            return await run_turn(
                dss,
                base_url,
                question,
                session_id=session_id,
                transaction_id=session_id,
            )

        return await run_load(
            questions,
            [int(n) for n in args.concurrency.split(",")],
            turn=turn,
            misses=partial(_misses, mock),
            max_turns=args.max_turns,
            warmup=args.warmup,
        )


async def _misses(mock: httpx.AsyncClient) -> set[str]:
    body = (await mock.get("/_bench/misses")).json()
    return set(body["transactionIds"])


async def _wait_until_up(client: httpx.AsyncClient, base_url: str) -> None:
    """The DSS has no health route; FastAPI's schema answers once it is up."""

    with anyio.fail_after(120):
        while True:
            try:
                if (await client.get(f"{base_url}/openapi.json")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await anyio.sleep(1)


def _trace_to_langfuse(keys: tuple[str, str]) -> None:
    """Point the container's tracing at Langfuse, as `run-local.sh` does for
    the local DSS. Set in this process's environment, so the credential is
    passed to the container by name, never on the command line."""

    token = base64.b64encode(f"{keys[0]}:{keys[1]}".encode()).decode()
    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:3000/api/public/otel"
    )
    os.environ.setdefault(
        "OTEL_EXPORTER_OTLP_HEADERS", f"Authorization=Basic%20{token}"
    )
    os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")


def _port(url: str) -> int:
    return httpx.URL(url).port or 80


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
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("speed", "load"),
        default="speed",
        help="speed: one turn at a time (default); load: several at once",
    )
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
    load = parser.add_argument_group("load mode")
    load.add_argument(
        "--concurrency", default="1,2,4,8", help="turns at once, per step"
    )
    load.add_argument("--cpus", default="1")
    load.add_argument("--memory", default="1g")
    load.add_argument("--image", default="dss:bench")
    load.add_argument("--port", type=int, default=8087, help="the container's DSS")
    load.add_argument("--no-build", action="store_true", help="use --image as built")
    return parser


if __name__ == "__main__":
    main()
