"""The two evidence ports.

They are separate because their rules are opposite, and one combined port could
carry neither correctly:

- `TurnSink` is the audit trail. It holds farmer content on purpose, it is
  required, and a write that fails **fails the turn** — a lost audit record is
  not an acceptable success.
- `TelemetrySink` holds stage, timing and outcome. It is optional, defaults to
  stdout so a bare deployment still works, and a write that fails is swallowed.

Farmer content reaching `TelemetrySink` is a bug; farmer content reaching
`TurnSink` is the requirement. Keeping the two impossible to confuse is the
whole reason there are two.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from dss.core.shared.models import TurnContext, TurnFinished, UserTurn


@runtime_checkable
class TurnSink(Protocol):
    """The record of the turn. Required."""

    def opened(self, ctx: TurnContext, turn: UserTurn) -> None: ...

    def closed(self, ctx: TurnContext, finished: TurnFinished) -> None: ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Stage outcomes. Optional, and never farmer content."""

    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None: ...
