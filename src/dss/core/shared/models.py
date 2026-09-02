"""The normalized turn envelope shared across core services.

``UserTurn`` is the request the Experience API hands the DSS, normalized. It
mirrors the wire contract in ``docs/DSS-API-Contract`` §2 (the header
``X-Session-Id`` lands here as ``session_id``, ``X-Trace-Id`` as ``trace_id``)
and the directional shape in ``DSS_ARCHITECTURE.md`` §5.1.

Two contract rules are enforced structurally rather than by prose:

- **No raw PII on the envelope.** Identity travels as an opaque ``subject_ref``
  (§2: "no phone, no name, no email, no JWT claims"). Nothing here can carry a
  real identifier, so nothing downstream can leak one.
- **Unknown fields are rejected** (§2). ``extra="forbid"`` turns a typo'd or
  smuggled field into a 422 at the boundary instead of silently ignored input.

The types are plain pydantic models — no framework import — so every core
service takes and returns them without a runtime (folder rule in ``CLAUDE.md``).
``history``'s entry shape is deliberately minimal; its full typing is deferred
(``DSS_ARCHITECTURE.md`` §8.3).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# lowercase per CONVENTIONS.md "Channel values: lowercase"; the contract's set.
Channel = Literal["web", "whatsapp", "voice", "sms"]

# History roles are "user" or "assistant" only (CONVENTIONS.md) — never
# "human"/"bot"/"farmer".
Role = Literal["user", "assistant"]


class SubjectRef(BaseModel):
    """Opaque identity reference. Never a phone, name, email, or JWT claim.

    ``user_id`` is a canonical id the caller maps from a real identifier and
    keeps in its own table; the DSS cannot reverse it. ``ref`` is the token a
    turn passes on to a Provider when acting on the farmer's behalf; an expired
    ``expires_at`` (RFC 3339) counts as absent. See API contract §2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = "anonymous"
    ref: str | None = None
    issuer: str | None = None
    expires_at: str | None = None


class GeoJSONGeometry(BaseModel):
    """Beckn GeoJSONGeometry v2.0 (RFC 7946, WGS-84).

    Coordinates are ``[longitude, latitude]`` — GeoJSON order. ``coordinates``
    is left loosely typed because a Point is a pair while a Polygon nests
    further; validating the nesting per ``type`` is deferred and not needed by
    any current core service.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: str
    coordinates: Any


class Location(BaseModel):
    """Where the turn is anchored. All fields optional (contract §2).

    ``region`` is ISO 3166-2 (e.g. ``IN-GJ``); ``area`` is free text, since
    there is no governed list of Indian districts to validate against.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    region: str | None = None
    area: str | None = None
    geometry: GeoJSONGeometry | None = None


class TurnHistoryEntry(BaseModel):
    """One prior turn. Minimal shape; full typing deferred (§8.3).

    Treated as untrusted data by any prompt that reads it — prior user turns are
    input, never instructions (``DSS_ARCHITECTURE.md`` §3.0).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str


class UserTurn(BaseModel):
    """A single normalized turn — the unit every core service reads.

    The query stays whole here; downstream stages read the labels the intent
    step produces, not a re-sliced query (intent spec: "the query stays whole in
    UserTurn").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1)
    source_lang: str  # BCP-47 (CONVENTIONS.md); membership checked at the edge.
    target_lang: str  # BCP-47
    channel: Channel
    session_id: str
    trace_id: str | None = None
    subject_ref: SubjectRef = Field(default_factory=SubjectRef)
    location: Location | None = None
    history: tuple[TurnHistoryEntry, ...] = ()
    response_max_chars: int | None = None
