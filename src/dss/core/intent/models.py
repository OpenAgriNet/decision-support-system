"""The intent contract — what a farmer is asking for (spec 0002).

IntentClassification lives here; it runs independently of moderation (they no longer
share a context — see ADR-0003). A turn can carry more than one ask (e.g. "wheat
price and will it rain?"), so an ``Intent`` is a tuple of ``Ask``s, each naming a
single subject on a single interaction type, plus one overall ``confidence``.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaMatch

# What a model leaves around a name it copied out of a sentence or a quoted
# example. Brackets and hyphens are not here: they can end a real name.
_PLACE_NAME_EDGE_JUNK = " \t\n'\"‘’“”,.;:"

# "2. " or "2) " in front of a name the model copied from our numbered list.
# A real name like "24 Parganas" has no dot or bracket after the number.
_LINE_NUMBER = re.compile(r"^\d+[.)]\s+")


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
    # The network's own enum (AgricultureResource `subjectCategories`) carries
    # `Practice` alongside this one. Only `Facility` is here, because only it has
    # a pack: `AgricultureFacility` *requires* `subjectCategories` to include
    # `Facility`, so the capability index keys it under a category intent could
    # not produce — a warehouse ask resolved to nothing with the pack sitting on
    # disk. Adding `Practice` before something serves it would only widen what
    # the classifier can emit and nothing can answer.
    FACILITY = "Facility"


class PlaceSource(StrEnum):
    """How a resolved place was decided.

    ``ASSERTED_AREA`` and ``ASSERTED_GEOMETRY`` both come from the calling
    platform's ``turn.location`` — told to us as fact, not said by the farmer
    this turn. They stay distinct because the area still goes through the
    same lookup a farmer-named place does; the geometry does not.
    """

    NAMED = "named"
    CARRIED = "carried"
    ASSERTED_AREA = "asserted_area"
    ASSERTED_GEOMETRY = "asserted_geometry"


class ResolvedPlace(BaseModel):
    """A place, as an ordered chain of ancestors, coarsest first, with no level
    words. "State", "District", "County" are English-and-India-shaped; ``within``
    lets an adopter fill in whatever levels their own geography uses.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    within: tuple[str, ...]
    geometry: Geometry
    source: PlaceSource


class AmbiguousPlace(BaseModel):
    """A named place that matched more than one area. `candidates` is the
    farmer's own words, unpicked — carried so a clarification question can
    list them without a second lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unresolved_name: str
    candidates: tuple[AreaMatch, ...]


class UnresolvedPlace(BaseModel):
    """A named place the index does not carry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unresolved_name: str


class Ask(BaseModel):
    """One thing the turn wants: a subject on a single interaction type.

    ``agriculture_subjects`` is the free-text specific ("potato", "PM-KISAN") and
    is ``None`` when the category needs no subject (e.g. "will it rain?").

    ``place`` is ``None`` when no place applies — nothing was named, no
    device location, no client area, or the ask needs none at all
    ("how do I grow potatoes"). It is only ever a failure type
    (``AmbiguousPlace``/``UnresolvedPlace``) when a name was actually given
    and the lookup could not turn it into one place.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agriculture_subjects: str | None = None
    subject_categories: SubjectCategory
    interaction_type: InteractionType
    place: ResolvedPlace | AmbiguousPlace | UnresolvedPlace | None = None


class ClassifiedAsk(BaseModel):
    """One ask as the LLM returns it — words only, no geometry.

    ``place_name`` is the free-text place the farmer named or asked about, in
    English. The resolver turns this into a ``ResolvedPlace``; the schema
    handed to the model never carries a geometry field, so it cannot invent
    one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agriculture_subjects: str | None = None
    subject_categories: SubjectCategory
    interaction_type: InteractionType
    place_name: str | None = None
    # True when place_name came from an earlier turn, not the latest query.
    # Only the model can tell: it read both, in whatever language they were.
    place_from_history: bool = False

    @field_validator("place_name")
    @classmethod
    def _trim_place_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = _LINE_NUMBER.sub("", value.strip(_PLACE_NAME_EDGE_JUNK))
        return trimmed.strip(_PLACE_NAME_EDGE_JUNK) or None


class IntentClassification(BaseModel):
    """The LLM's structured-output schema for a turn — the raw finding before
    place resolution builds the domain ``Intent`` from it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asks: tuple[ClassifiedAsk, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Intent(BaseModel):
    """A finding about a turn. An empty ``asks`` means the classifier recognised
    nothing — an outcome worth acting on, not an error.

    ``frozen`` blocks attribute assignment; ``asks`` is a tuple so it is immutable
    in fact, not just by convention (see spec 0002).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asks: tuple[Ask, ...] = ()
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
