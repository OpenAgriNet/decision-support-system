"""What composition produces."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dss.core.shared.models import OutputContent, Source


class ComposedAnswer(BaseModel):
    """The written answer and the sources behind it.

    Every `source_ids` entry on a block must name a source listed here — a
    citation marker pointing at nothing is worse than no marker.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: tuple[OutputContent, ...]
    sources: tuple[Source, ...] = ()


class ClarificationText(BaseModel):
    """Fixed questions a farmer reads when a turn needs more from them.
    Loaded from config, like `Identity`.

    `unknown_place` and `ambiguous_place_header` are `str.format` templates —
    e.g. `"I could not find {name}."`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    needs_place: str
    unknown_place: str
    ambiguous_place_header: str
    grouped_place_header: str
    # Closes a grouped list that was cut at `max_choices`.
    more_places_hint: str
    # More choices than this are grouped one level up before they are listed.
    max_choices: int = Field(default=5, gt=0)
