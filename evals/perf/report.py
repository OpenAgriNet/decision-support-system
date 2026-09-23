"""A run's turns, summed up as typical (p50), slow (p95) and worst (max).

Built as a plain dict, ready for JSON, and the text table is rendered from
the same dict, so the printout and the file cannot disagree.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from evals.perf.load import StepResult
from evals.perf.runner import RunResult
from evals.perf.stats import summarise


def figures(
    result: RunResult, *, commit: str | None = None, machine: dict | None = None
) -> dict:
    """`commit` and `machine` are read by the caller: this stays a pure
    function of what it is given."""

    counted = [t for t in result.turns if not t.missed]
    traced = [t.facts for t in counted if t.facts is not None]
    trusted = [f for f in traced if not f.flat]

    stages: dict[str, list[float]] = defaultdict(list)
    for facts in trusted:
        for stage, seconds in facts.stages.items():
            stages[stage].append(seconds)
    # Tokens do not depend on the tree's shape, so flat traces count here.
    tokens: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"input": [], "output": []}
    )
    for facts in traced:
        for call in facts.calls:
            tokens[call.agent]["input"].append(call.input)
            tokens[call.agent]["output"].append(call.output)

    return {
        "run": {
            # What the server ran with, as its traces recorded it.
            "models": next((f.models for f in traced if f.models), {}),
            "commit": commit,
            "machine": machine or {},
            "truncated": result.truncated,
        },
        "turns": {
            "timed": len(result.turns),
            "missed": len(result.turns) - len(counted),
            "by_status": dict(Counter(str(t.timing.status) for t in counted)),
            "no_trace": len(counted) - len(traced),
            "flat_traces": len(traced) - len(trusted),
        },
        "first_delta_s": _summary(t.timing.first_delta_s for t in counted),
        "total_s": _summary(t.timing.total_s for t in counted),
        "stages_s": {stage: _summary(values) for stage, values in stages.items()},
        "tokens_per_call": {
            agent: {kind: _summary(values) for kind, values in counts.items()}
            for agent, counts in tokens.items()
        },
    }


def load_figures(
    steps: list[StepResult],
    *,
    limits: dict,
    machine: dict,
    commit: str | None,
) -> dict:
    """Load mode's report: one row per step, and what the DSS ran on.
    `limits` is what the container actually got, read back from it."""

    return {
        "run": {"commit": commit, "machine": machine, "limits": limits},
        "steps": [
            {
                "concurrency": step.concurrency,
                "turns_per_min": step.turns_per_min,
                "missed": step.missed,
                "truncated": step.truncated,
                "by_status": dict(Counter(str(t.status) for t in step.timings)),
                "first_delta_s": _summary(t.first_delta_s for t in step.timings),
                "total_s": _summary(t.total_s for t in step.timings),
            }
            for step in steps
        ],
    }


def render_load_text(report: dict) -> str:
    """Load mode's report as a table: one row per step."""

    run = report["run"]
    lines = [
        f"{'N':>4}  {'turns/min':>9}  {'first piece p50/p95/max (s)':>28}"
        f"  {'total p50/p95/max (s)':>22}  missed  ended",
    ]
    for step in report["steps"]:
        mark = "*" if step["truncated"] else " "
        ended = ", ".join(f"{s} {n}" for s, n in step["by_status"].items())
        lines.append(
            f"{step['concurrency']:>4}{mark} {step['turns_per_min']:>9.1f}"
            f"  {_triple(step['first_delta_s']):>28}"
            f"  {_triple(step['total_s']):>22}  {step['missed']:>6}  {ended}"
        )
    if any(step["truncated"] for step in report["steps"]):
        lines.append("* cut short by the turn limit: fewer turns than the others")
    cpus = run["limits"].get("cpus", 0)
    memory = run["limits"].get("memory_gib", 0)
    lines += [
        "",
        f"limits: {cpus:g} CPU · {memory:.1f} GiB",
        f"commit: {_short_commit(run['commit'])}",
        f"machine: {_machine_line(run['machine'])}",
        "Compare runs made on the same machine, close together in time.",
    ]
    return "\n".join(lines)


def _triple(summary: dict) -> str:
    values = (summary["p50"], summary["p95"], summary["max"])
    return " / ".join("-" if v is None else f"{v:.2f}" for v in values)


def write_json(report: dict, result: RunResult, directory: Path) -> Path:
    """The report plus one raw row per timed turn, named by when it was
    written and the commit it ran on, so a run is found by either."""

    rows = [
        {
            "question_id": t.question_id,
            "category": t.category,
            "repeat": t.repeat,
            "session_id": t.session_id,
            "missed": t.missed,
            **asdict(t.timing),
        }
        for t in result.turns
    ]
    return save_json({**report, "turn_rows": rows}, directory)


def save_json(report: dict, directory: Path, *, kind: str = "speed") -> Path:
    """Write a report as `<utc>-<kind>-<commit>.json`, found by time, mode or
    commit."""

    directory.mkdir(parents=True, exist_ok=True)
    written_at = f"{datetime.now(UTC):%Y-%m-%dT%H-%M-%SZ}"
    path = directory / f"{written_at}-{kind}-{report['run']['commit']}.json"
    path.write_text(json.dumps(report, indent=1))
    return path


# Stages in the order a turn runs them. A network call prints under the stage
# that makes it, so `/discover` does not read as `discovery` twice.
_STAGE_ROWS = (
    ("intent", "  intent"),
    ("moderation", "  moderation"),
    ("enrichment", "  enrichment"),
    ("discovery", "  discovery"),
    ("discover", "    ↳ network /discover"),
    ("planner", "  planner"),
    ("select", "    ↳ network /select"),
    ("composer", "  composer"),
)


def render_text(report: dict) -> str:
    """The report as a plain table for the terminal."""

    run, turns = report["run"], report["turns"]
    lines = [
        f"{'':<24}{'p50':>9}{'p95':>9}{'max':>9}{'n':>6}",
        _row("first piece (s)", report["first_delta_s"], "{:.2f}"),
        _row("total (s)", report["total_s"], "{:.2f}"),
        "",
        "stage (s)",
    ]
    stages = report["stages_s"]
    known = dict(_STAGE_ROWS)
    lines += [
        _row(label, stages[stage], "{:.3f}")
        for stage, label in _STAGE_ROWS
        if stage in stages
    ]
    # Any stage not listed above, after the known ones, so none vanishes.
    lines += [
        _row(f"  {stage}", summary, "{:.3f}")
        for stage, summary in stages.items()
        if stage not in known
    ]
    lines += ["", "tokens per model call"]
    for agent, kinds in report["tokens_per_call"].items():
        lines += [
            _row(f"  {agent} {kind}", summary, "{:.0f}")
            for kind, summary in kinds.items()
        ]
    lines += [
        "",
        f"turns: {turns['timed']} timed, {turns['missed']} missed, "
        f"{turns['no_trace']} without a trace, {turns['flat_traces']} flat",
        "ended: " + ", ".join(f"{s} {n}" for s, n in turns["by_status"].items()),
        f"models: {_models_line(run['models'])}",
        f"commit: {_short_commit(run['commit'])}",
        f"machine: {_machine_line(run['machine'])}",
    ]
    if run["truncated"]:
        lines.append("NOTE: the turn limit stopped the run early.")
    lines.append("Compare runs made on the same machine, close together in time.")
    return "\n".join(lines)


def _models_line(models: dict[str, str]) -> str:
    if len(models) == 4 and len(set(models.values())) == 1:
        return f"{next(iter(models.values()))} (all four agents)"
    return ", ".join(f"{agent}={model}" for agent, model in models.items())


def _short_commit(commit: str | None) -> str:
    """12 characters are enough to find a commit; the JSON keeps all 40."""

    if not commit:
        return "unknown"
    sha, dirty, _ = commit.partition("-dirty")
    return sha[:12] + dirty


def _machine_line(machine: dict) -> str:
    parts = dict(machine)
    shown = [
        parts.pop("platform", None),
        parts.pop("processor", None),
        f"{parts.pop('cpu_count')} cores" if "cpu_count" in parts else None,
        f"Python {parts.pop('python')}" if "python" in parts else None,
        *map(str, parts.values()),
    ]
    return " · ".join(part for part in shown if part)


def _row(label: str, summary: dict, number: str) -> str:
    def cell(value) -> str:
        return f"{'-' if value is None else number.format(value):>9}"

    return (
        f"{label:<24}{cell(summary['p50'])}{cell(summary['p95'])}"
        f"{cell(summary['max'])}{summary['n']:>6}"
    )


def _summary(values) -> dict:
    return asdict(summarise([v for v in values if v is not None]))
