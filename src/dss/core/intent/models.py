"""The intent contract — what a farmer is asking for (spec 0002).

Classification lives here; it runs independently of moderation (they no longer
share a context — see ADR-0003). A turn can carry more than one ask (e.g. "wheat
price and will it rain?"), so an ``Intent`` is a tuple of ``Ask``s, each naming a
single subject on a single interaction type, plus one overall ``confidence``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InteractionType(StrEnum):
    """What the farmer wants done with a subject — a turn may mix several."""

    # explain/guide: crop advisory, cattle-health guidance, scheme explanation
    ADVISE = "advise"
    # look up a value/record/status: weather, mandi price, milk record, eligibility
    OBSERVE = "observe"
    # perform an action: book a service, apply for a scheme, submit a grievance
    ACT = "act"


class SubjectCategory(StrEnum):
    """The closed top-level subject taxonomy for an ask."""

    CROP = "Crop"
    LIVESTOCK = "Livestock"
    WEATHER = "Weather"
    MARKET = "Market"
    SCHEME = "Scheme"


class Ask(BaseModel):
    """One thing the turn wants: a subject on a single interaction type.

    ``agriculture_subjects`` is the free-text specific ("potato", "PM-KISAN") and
    is ``None`` when the category needs no subject (e.g. "will it rain?").
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agriculture_subjects: str | None = None
    subject_categories: SubjectCategory
    interaction_type: InteractionType


class Intent(BaseModel):
    """A finding about a turn. An empty ``asks`` means the classifier recognised
    nothing — an outcome worth acting on, not an error.

    ``frozen`` blocks attribute assignment; ``asks`` is a tuple so it is immutable
    in fact, not just by convention (see spec 0002).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asks: tuple[Ask, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
