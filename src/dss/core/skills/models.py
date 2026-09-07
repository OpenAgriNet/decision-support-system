"""The skills contract (design v2 §6.3). Data shapes only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Skill(BaseModel):
    """One piece of reasoning guidance. ``guidance`` goes verbatim into the
    planner prompt; ``domain`` is what it applies to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    domain: str
    guidance: str


class Skills(BaseModel):
    """The skills selected for this turn. Empty is valid — a turn may need none."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    selected: tuple[Skill, ...] = ()
