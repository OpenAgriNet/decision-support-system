"""The driving port — the only thing the transport is allowed to call.

Streaming never crosses this seam. The runner produces events; whether they
become SSE frames or a single JSON body is the transport's business. That is why
`run` returns an iterator rather than taking a sink to push into: the transport
keeps control of flush timing, and the non-streaming case is a drain rather than
a buffer-and-hope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from dss.core.shared.models import TurnContext, TurnEvent, UserTurn


@runtime_checkable
class TurnRunner(Protocol):
    def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]: ...
