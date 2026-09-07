"""Server-Sent Events framing.

This module owns two things and no others: what each event is called, and the
sequence number. Neither is a domain concern — the runner produces events and
never learns whether they were streamed.

There is deliberately no `id:` line. An `id:` advertises `Last-Event-ID`, and
this stream cannot be resumed: on a dropped connection the DSS finishes the turn
server-side and the caller recovers it from session history.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from dss.adapters.http.v1 import mapping, schema
from dss.core.shared.models import (
    Claim,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnStarted,
    TurnStatus,
)

CREATED = "turn.created"
CLAIM = "claim.completed"
COMPLETED = "turn.completed"
FAILED = "turn.failed"


class Stream:
    """One turn's worth of frames. Holds the counter, so the counter cannot be
    reused across turns by accident."""

    def __init__(
        self,
        ctx: TurnContext,
        *,
        response_id: str,
        clock: Callable[[], datetime],
    ) -> None:
        self._ctx = ctx
        self._response_id = response_id
        self._clock = clock
        self._seq = 1  # the contract sets sequenceNumber minimum 1

    def frame(self, event: TurnEvent) -> bytes:
        """Render one event, consuming the next sequence number."""

        name, body = self._render(event, seq=self._seq)
        self._seq += 1
        payload = body.model_dump_json(by_alias=True, exclude_none=True)
        return f"event: {name}\ndata: {payload}\n\n".encode()

    def _render(self, event: TurnEvent, *, seq: int) -> tuple[str, schema.TurnResponse]:
        common = {
            "now": self._clock(),
            "seq": seq,
            "response_id": self._response_id,
        }
        if isinstance(event, TurnStarted):
            return CREATED, mapping.to_created_frame(self._ctx, **common)
        if isinstance(event, Claim):
            return CLAIM, mapping.to_claim_frame(event, self._ctx, **common)
        return _terminal_name(event), mapping.to_terminal_frame(
            event, self._ctx, **common
        )


def _terminal_name(event: TurnFinished) -> str:
    """The only place the wire cares which way a turn went.

    A refusal, a no-match, and a request for more input are all turns the DSS
    completed. Only a dependency failure is a failed turn.
    """

    if event.outcome.status is TurnStatus.UNAVAILABLE:
        return FAILED
    return COMPLETED
