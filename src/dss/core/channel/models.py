"""What composition produces."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dss.core.shared.models import OutputContent, Source


class ComposedAnswer(BaseModel):
    """The written answer and the sources behind it.

    Every `source_ids` entry on a block must name a source listed here — a
    citation marker pointing at nothing is worse than no marker.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: tuple[OutputContent, ...]
    sources: tuple[Source, ...] = ()
