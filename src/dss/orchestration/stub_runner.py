"""STUB(#81) — a runner that answers without thinking.

It reads the turn for nothing, on purpose. If the transport passes its cases
against this, the transport is proven independent of everything below it.

Replaced by the real runner once core lands; the port does not change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dss.core.shared.models import (
    Claim,
    Source,
    SourceKind,
    TextBlock,
    TurnContext,
    TurnEvent,
    TurnFinished,
    TurnOutcome,
    TurnStarted,
    TurnStatus,
    UserTurn,
)

_CONTENT = (
    TextBlock(
        text="Wheat is trading at Rs 2,275 per quintal at Anand mandi this week.",
        source_ids=("src_1",),
    ),
    TextBlock(
        text="Prices were last updated this morning.",
        source_ids=("src_1",),
    ),
)

_SOURCES = (
    Source(
        id="src_1",
        name="Agmarknet",
        kind=SourceKind.PROVIDER,
        url="https://agmarknet.gov.in/",
    ),
)


class StubRunner:
    ANSWER = TurnFinished(
        outcome=TurnOutcome(status=TurnStatus.ANSWERED, confidence=92),
        content=_CONTENT,
        sources=_SOURCES,
    )

    async def run(self, turn: UserTurn, ctx: TurnContext) -> AsyncIterator[TurnEvent]:
        yield TurnStarted()
        for block in _CONTENT:
            yield Claim(content=block, sources=_SOURCES)
        yield self.ANSWER
