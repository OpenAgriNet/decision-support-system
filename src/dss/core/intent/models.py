"""The intent contract — what a farmer is asking for (spec 0002).

Classification lives here; it runs independently of moderation (they no longer
share a context — see ADR-0003). An ``Intent`` names *what* the turn is about
along three axes:

- ``subject_categories`` — the closed top-level taxonomy (crop, livestock, …).
- ``agriculture_subjects`` — free-text specifics ("potato", "wheat rust") the
  closed enum cannot enumerate.
- ``capabilities`` — whether the turn wants Knowledge (an answer) or a Service
  (an action performed on the user's behalf).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SubjectCategory(StrEnum):
    """The closed top-level subject taxonomy for a turn."""

    CROP = "Crop"
    LIVESTOCK = "Livestock"
    WEATHER = "Weather"
    MARKET = "Market"
    SCHEME = "Scheme"


class Capability(StrEnum):
    """What kind of help the turn wants."""

    KNOWLEDGE = "Knowledge"  # answer a question / give advice
    SERVICE = "Service"  # perform an action (book, register, apply)


class Intent(BaseModel):
    """A finding about a turn. Empty lists mean the classifier recognised nothing
    on that axis — an outcome worth acting on, not an error.

    ``frozen`` blocks attribute assignment; it does not deep-freeze the list
    fields (see spec 0002). Nothing caches an ``Intent`` yet, so that is fine.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_categories: list[SubjectCategory] = Field(default_factory=list)
    agriculture_subjects: list[str] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
