"""The rules a turn's events keep, checked over a whole drained turn.

A client renders an answer as it arrives because of these, so every test that
drains a turn checks them, whichever path the turn took.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    TurnEvent,
    TurnFinished,
    TurnStarted,
)


def assert_well_formed(events: Sequence[TurnEvent]) -> None:
    """`TurnStarted` first if sent, `TurnFinished` last and once; each
    `Claim` is the deltas since the one before; a finished turn has no
    unclaimed deltas, and holds every claimed block in order."""

    starts = [i for i, event in enumerate(events) if isinstance(event, TurnStarted)]
    finishes = [i for i, event in enumerate(events) if isinstance(event, TurnFinished)]
    assert starts in ([], [0]), f"TurnStarted at {starts}"
    assert finishes == [len(events) - 1], f"TurnFinished at {finishes}"

    since_last_claim: list[str] = []
    claimed = []
    for event in events:
        if isinstance(event, ClaimDelta):
            since_last_claim.append(event.text)
        elif isinstance(event, Claim):
            assert "".join(since_last_claim) == event.content.text
            since_last_claim = []
            claimed.append(event.content)
    assert since_last_claim == [], "deltas no Claim closed"

    finished = events[-1]
    assert isinstance(finished, TurnFinished)
    if claimed:
        assert finished.content == tuple(claimed)
