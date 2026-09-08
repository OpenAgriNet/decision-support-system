"""STUB(#85) — the turn record, kept in a dict.

Replaced by a durable store. The shape is the point: a turn is opened when it
starts and closed when it ends, so a turn that crashed mid-flight still leaves a
record — which is the case an audit trail exists for.
"""

from __future__ import annotations

from dataclasses import dataclass

from dss.core.shared.models import TurnContext, TurnFinished, UserTurn


@dataclass
class Record:
    turn: UserTurn | None = None
    finished: TurnFinished | None = None


class MemoryTurnSink:
    def __init__(self) -> None:
        self.records: dict[str, Record] = {}

    def opened(self, ctx: TurnContext, turn: UserTurn) -> None:
        self.records.setdefault(ctx.trace_id, Record()).turn = turn

    def closed(self, ctx: TurnContext, finished: TurnFinished) -> None:
        self.records.setdefault(ctx.trace_id, Record()).finished = finished
