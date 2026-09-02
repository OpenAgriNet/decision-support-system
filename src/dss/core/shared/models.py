"""The request envelope — the normalized turn the DSS receives (spec 0002, §5.1).

``UserTurn`` carries the ``(original_query, enriched_query)`` pair from the start
(architecture §3.0): moderation evaluates the enriched query, and until
enrichment lands ``enriched_query`` mirrors ``original_query``. Defining both now
means later slices do not have to re-thread the second field through every
signature.

The conversation ``history``, ``location``, and the actor's ``reference`` token
are typed here because the Experience-API envelope carries them and the
orchestration boundary (``orchestration/envelope.py``) normalizes an inbound
request into this shape.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# BCP-47 primary subtag plus optional subtags — enough to reject full names like
# "gujarati" while accepting "gu", "hi", "en", "en-IN" (CONVENTIONS.md).
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")


class ReferenceToken(BaseModel):
    """A provider-call credential the DSS passes on but does not itself consume.
    An expired token is treated as absent at the mapping boundary (spec: "An
    expired ref counts as absent"), so anything that reaches here is live."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    issuer: str | None = None
    expires_at: datetime | None = None


class UserDetails(BaseModel):
    """Actor identifiers. All optional — a turn need not be attributed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str | None = None
    phone: str | None = None
    reference: ReferenceToken | None = None


class ConversationMessage(BaseModel):
    """One prior turn in the thread. Moderation and intent read the recent history
    so a follow-up ("And potato?") resolves against what came before."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user", "assistant"]
    text: str


class Geometry(BaseModel):
    """GeoJSON-style point. ``coordinates`` is ``[lon, lat]`` (spec)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["Point"] = "Point"
    coordinates: list[float]


class Location(BaseModel):
    """Where the turn is grounded. All parts optional — the envelope marks the
    whole block optional."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: str | None = None  # ISO 3166-2, e.g. "IN-GJ"
    area: str | None = None  # local name, e.g. "Anand"
    geometry: Geometry | None = None


class UserTurn(BaseModel):
    """One normalized turn from the Experience API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original_query: str
    enriched_query: str
    session_id: str
    source_lang: str  # language the user spoke/typed (BCP-47)
    target_lang: str  # language the response should come back in (BCP-47)
    channel: str  # web / whatsapp / voice / ... (lowercase)
    user: UserDetails = Field(default_factory=UserDetails)
    history: list[ConversationMessage] = Field(default_factory=list)
    location: Location | None = None
    response_max_chars: int | None = None

    @field_validator("source_lang", "target_lang")
    @classmethod
    def _bcp47(cls, value: str) -> str:
        if not _BCP47.fullmatch(value):
            raise ValueError(
                f"language code {value!r} is not BCP-47 — use 'gu'/'hi'/'en', "
                "never a full name like 'gujarati'"
            )
        return value
