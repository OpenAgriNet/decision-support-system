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
from pydantic.json_schema import SkipJsonSchema

from dss.core.shared.models import Geometry, ProviderAction
from dss.ports.area_lookup import AreaMatch

# What a model leaves around a name it copied out of a sentence or a quoted
# example. Brackets and hyphens are not here: they can end a real name.
_PLACE_NAME_EDGE_JUNK = " \t\n'\"‘’“”,.;:"  # noqa: RUF001 - curly quotes on purpose

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
    # The Grievance API pack's type: a complaint filed or followed up against
    # a scheme. Kept apart from `Scheme` because it ends in a form, not an answer.
    GRIEVANCE = "Grievance"


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
    # The user named a whole state, and this is their own place inside it.
    # The answer must say so, so a wrong guess can be corrected next turn.
    NEAR_USER = "near_user"


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
    """A named place the index does not carry, or one too big to search."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unresolved_name: str
    # Set when the name is a whole state or region ("Maharashtra"). The farmer
    # is asked for a smaller place in it, not told it was not found.
    region: str | None = None


class Scheme(BaseModel):
    """One government scheme as the catalog knows it. ``code`` is the network's
    scheme code, verbatim: the key a scheme pack selects its form and its
    provider by."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    name: str


class GuessedScheme(BaseModel):
    """The closest listed scheme, when the farmer did not clearly name one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Scheme


class UnlistedScheme(BaseModel):
    """A scheme the farmer named that the catalog does not hold."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class SchemeConfidence(StrEnum):
    HIGH = "high"
    LOW = "low"


class UnclearAction(StrEnum):
    """The farmer wants something done, but their words do not say which.
    Kept apart from `ProviderAction` so it can never name a form."""

    UNCLEAR = "unclear"


class SchemeEntry(BaseModel):
    """One catalog row, as the intent model is shown it: the scheme and every
    other way a farmer may name it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Scheme
    aliases: tuple[str, ...] = ()


class Ask(BaseModel):
    """One thing the turn wants: a subject on a single interaction type.

    ``agriculture_subjects`` is the free-text specific ("potato", "PM-KISAN") and
    is ``None`` when the category needs no subject (e.g. "will it rain?").

    ``place`` is ``None`` when no place applies — nothing was named, no
    device location, no client area, or the ask needs none at all
    ("how do I grow potatoes"). It is only ever a failure type
    (``AmbiguousPlace``/``UnresolvedPlace``) when a name was actually given
    and the lookup could not turn it into one place.

    ``scheme`` is a ``Scheme`` only when the farmer named one the catalog
    holds. A ``GuessedScheme`` must be confirmed with the farmer before it is
    used. An ``UnlistedScheme`` is a name we could not find.

    ``action`` is ``None`` when the farmer wants an answer and nothing done.
    It is ``UnclearAction`` when they want something done and did not say
    which: only then are they asked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agriculture_subjects: str | None = None
    subject_categories: SubjectCategory
    interaction_type: InteractionType
    place: ResolvedPlace | AmbiguousPlace | UnresolvedPlace | None = None
    scheme: Scheme | GuessedScheme | UnlistedScheme | None = None
    action: ProviderAction | UnclearAction | None = None

    @property
    def known_action(self) -> ProviderAction | None:
        return self.action if isinstance(self.action, ProviderAction) else None


# The docstrings of ClassifiedAsk and IntentClassification go to the model as
# the tool description. Write them for the model; notes for us go in comments.
# No geometry field here on purpose: the model cannot invent one, and the
# resolver turns place_name into a ResolvedPlace.
class ClassifiedAsk(BaseModel):
    """One ask from the user's query, in words only.

    ``place_name`` is the place the user named or asked about, in English,
    or null when the ask names no place.

    ``scheme_code`` is the code of the listed scheme this ask is about, or
    null when it names none of them.

    ``scheme_confidence`` is high when the user's words name that scheme: its
    name, its code or another listed name, even misspelt. It is low when you
    only picked the closest one.

    ``unlisted_scheme_name`` is the scheme the user named, in their words,
    when it is none of the listed schemes. Null otherwise.

    ``action`` is what the user wants done about a complaint: support to
    raise a new one, status to check one already raised. Null when their
    words do not say which, or the ask is not a complaint.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    agriculture_subjects: str | None = None
    subject_categories: SubjectCategory
    interaction_type: InteractionType
    place_name: str | None = None
    # True when place_name came from an earlier turn, not the latest query.
    # Only the model can tell: it read both, in whatever language they were.
    place_from_history: bool = False
    scheme_code: str | None = None
    # Low when left out: a scheme the model did not vouch for is confirmed.
    scheme_confidence: SchemeConfidence = SchemeConfidence.LOW
    unlisted_scheme_name: str | None = None
    action: ProviderAction | UnclearAction | None = None
    # Set by `classify_intent` from the catalog, never by the model: hidden
    # from the schema it is shown, so a code it invents never becomes a scheme.
    scheme: SkipJsonSchema[Scheme | GuessedScheme | UnlistedScheme | None] = None

    @field_validator("place_name")
    @classmethod
    def _trim_place_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = _LINE_NUMBER.sub("", value.strip(_PLACE_NAME_EDGE_JUNK))
        return trimmed.strip(_PLACE_NAME_EDGE_JUNK) or None

    @field_validator("unlisted_scheme_name")
    @classmethod
    def _drop_blank_scheme_name(cls, value: str | None) -> str | None:
        return (value or "").strip() or None


# The raw model output. resolve_places turns it into an Intent, below.
class IntentClassification(BaseModel):
    """Every ask in the user's query, each with the place the user named for
    it, and how sure you are.
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
