"""Builds a streamed answer's events so they agree with each other.

The rules a client relies on to render an answer as it arrives:

- the deltas since the last `Claim`, joined, are that `Claim`'s text;
- the `TurnFinished` holds every claimed block, in order, and the sources
  they cite.

Every layer that streams an answer builds its events here, so no layer can
send a `Claim` that disagrees with the pieces before it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    OutputContent,
    Source,
    TurnFinished,
    TurnOutcome,
)


class AnswerStream:
    """One streamed answer: pieces, then a `Claim` per block, then the
    `TurnFinished`."""

    def __init__(self) -> None:
        self._pieces: list[str] = []
        self._claims: list[Claim] = []

    @property
    def written(self) -> str:
        """The pieces since the last claim: the text the next claim holds."""

        return "".join(self._pieces)

    def add_piece(self, text: str) -> ClaimDelta:
        self._pieces.append(text)
        return ClaimDelta(text=text)

    def claim(self, block: OutputContent, sources: Sequence[Source]) -> Claim:
        """Close the block the pieces since the last claim wrote. `block`
        gives its type and citations; its text is always the pieces'."""

        claim = Claim(
            content=block.model_copy(update={"text": self.written}),
            sources=tuple(sources),
        )
        self._claims.append(claim)
        self._pieces = []
        return claim

    def finish(self, outcome: TurnOutcome) -> TurnFinished:
        return TurnFinished(
            outcome=outcome,
            content=tuple(claim.content for claim in self._claims),
            sources=_dedupe_by_id(
                source for claim in self._claims for source in claim.sources
            ),
        )


def _dedupe_by_id(sources: Iterable[Source]) -> tuple[Source, ...]:
    """In first-seen order, one per id."""

    first_by_id: dict[str, Source] = {}
    for source in sources:
        first_by_id.setdefault(source.id, source)
    return tuple(first_by_id.values())
