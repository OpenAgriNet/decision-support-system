"""Evidence written to disk as JSON Lines.

A stand-in for the external evidence API, which does not exist yet. The
destination is a setting (`Settings.evidence_url`); until something is listening
there, records land in files under `Settings.evidence_dir`.

One record per line, appended and flushed. `tail -f`, `grep` and `jq` all work,
and an appender never rewrites what is already written.

**The two files are two different tiers** (`api-contract.md` §6), and the
difference is not cosmetic:

- `telemetry.jsonl` — stage, outcome, timing. **No user content.** Broad access,
  long retention. The port's shape enforces this: `stage()` is handed a short
  string, never the turn.
- `turns.jsonl` — the question, the answer, the refusals. Farmer content **on
  purpose** — it is the audit trail, and metadata alone cannot answer "what went
  wrong". Restricted access, short retention.

`geometry` is dropped on write. `region` and `area` are kept. A stable user id
beside a precise point, across many turns, is a home address.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dss.core.shared.models import TurnContext, TurnFinished, UserTurn


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class _JsonLines:
    """Append-only writer. Creates its parent directory on first use."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = _now) -> None:
        self._path = Path(path)
        self._clock = clock

    def _write(self, record: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"recorded_at": _stamp(self._clock()), **record}, ensure_ascii=False
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _keys(self, ctx: TurnContext) -> dict[str, Any]:
        # `trace_id` is the caller's transactionId, so recording both would be
        # the same fact twice.
        return {
            "trace_id": ctx.trace_id,
            "session_id": ctx.session_id,
            "message_id": ctx.message_id,
        }


class FileTelemetrySink(_JsonLines):
    """The metrics tier. Never user content."""

    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None:
        self._write({**self._keys(ctx), "stage": name, "outcome": outcome})


class FileTurnSink(_JsonLines):
    """The audit trail. Holds the question and the answer, by design."""

    def opened(self, ctx: TurnContext, turn: UserTurn) -> None:
        self._write({**self._keys(ctx), "event": "opened", "turn": _turn(turn)})

    def closed(self, ctx: TurnContext, finished: TurnFinished) -> None:
        recorded = finished.model_dump(mode="json")
        self._write({**self._keys(ctx), "event": "closed", **recorded})


def _turn(turn: UserTurn) -> dict[str, Any]:
    """The turn as recorded — everything except the precise point."""

    recorded = turn.model_dump(mode="json")
    location = recorded.get("location")
    if location is not None:
        location.pop("geometry", None)
    return recorded
