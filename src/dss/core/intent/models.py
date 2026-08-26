"""The intent contract — what a farmer is asking for (spec 0002).

Classification lives here; moderation consumes an ``Intent`` and judges harm.
The two policies in this slice (profanity filter, delete-command) read only the
turn, but ``Intent`` is a required root of the moderation context, so it is
defined here from the start rather than retrofitted later.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    """What the farmer wants done — a turn may carry more than one."""

    ADVISORY = "advisory"  # "when should I sow wheat?"
    LOOKUP = "lookup"  # "what's today's mandi price?"
    ACT = "act"  # "book a soil test"


class Intent(BaseModel):
    """A finding about a turn. ``domains: []`` means the classifier recognised
    nothing — an outcome worth acting on, not an error.

    ``frozen`` blocks attribute assignment; it does not deep-freeze the list
    fields (see spec 0002). Nothing caches an ``Intent`` yet, so that is fine.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    domains: list[str] = Field(default_factory=list)
    subdomains: list[str] = Field(default_factory=list)
    action_types: list[ActionType] = Field(default_factory=list)
    entities: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
