"""Domain language. Plain Pydantic — no framework, no vendor SDK, no I/O.

Both rims of the hexagon depend on these types, so they live in `shared/`
rather than with one logical function.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Role(StrEnum):
    """Who said it. History is data the model reads, never instructions."""

    USER = "user"
    ASSISTANT = "assistant"


class Channel(StrEnum):
    """Where the turn came from. The DSS writes differently for each — voice gets
    words made for speaking, chat words made for reading."""

    WEB = "web"
    WHATSAPP = "whatsapp"
    VOICE = "voice"
    SMS = "sms"


class HistoryEntry(BaseModel):
    """One earlier message in the thread, flattened to text.

    The wire carries typed content parts; the core reads prose, so the parts are
    joined at the mapping boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Role
    content: str


class Point(BaseModel):
    """A location as named floats.

    GeoJSON carries `[lon, lat]` positionally, which the current deployments
    send reversed. Naming the axes means the ordering exists only in the wire
    mapping — inside the hexagon the mistake is unrepresentable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    lon: float
    lat: float


class Location(BaseModel):
    """Where the turn is grounded. Every part optional."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: str | None = None  # ISO 3166-2, e.g. "IN-GJ"
    area: str | None = None  # local name, e.g. "Anand"
    geometry: Point | None = None


ANONYMOUS = "anonymous"


class UserTurn(BaseModel):
    """One normalized turn, as the core sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    source_lang: str  # BCP 47 — what the user spoke or typed
    target_lang: str  # BCP 47 — what the answer must come back in
    channel: Channel
    user_id: str = ANONYMOUS
    history: tuple[HistoryEntry, ...] = ()
    location: Location | None = None
    response_max_chars: int | None = None


class TurnContext(BaseModel):
    """The two ids a turn is filed under, and nothing else.

    `trace_id` is per turn and is the evidence key. `session_id` spans many
    turns and belongs to the caller. `transaction_id` is the caller's own
    correlation id — echoed back, never interpreted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    session_id: str
    transaction_id: str | None = None
    message_id: str | None = None


class TurnStatus(StrEnum):
    """How a turn ended. One axis: `unavailable` is the execution-failure value,
    so no second field is needed to tell a refusal from a crash."""

    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    REJECTED = "rejected"
    NO_MATCH = "no_match"
    REQUIRES_INPUT = "requires_input"
    UNAVAILABLE = "unavailable"


class Cause(StrEnum):
    """Why a turn ended the way it did.

    Closed here on purpose: the core may only emit causes it knows. The wire set
    is documented as open, which is a promise to callers about future releases —
    not a licence to return a free string.
    """

    # harm
    UNSAFE_ILLEGAL = "unsafe_illegal"
    ROLE_OBFUSCATION = "role_obfuscation"
    POLITICAL_CONTROVERSIAL = "political_controversial"
    EXTERNAL_REFERENCE = "external_reference"
    ADOPTER_POLICY = "adopter_policy"
    # scope
    DOMAIN_UNMAPPED = "domain_unmapped"
    INTENT_LOW_CONFIDENCE = "intent_low_confidence"
    UNSUPPORTED_ACTION_TYPE = "unsupported_action_type"
    # infrastructure
    MODERATION_UNAVAILABLE = "moderation_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


class SourceKind(StrEnum):
    PROVIDER = "provider"
    DOCUMENT = "document"
    TOOL = "tool"


class Source(BaseModel):
    """Where a fact came from, named as the farmer sees it. Which internal
    capability produced it is telemetry, not wire."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    kind: SourceKind
    url: str | None = None


class TextBlock(BaseModel):
    """One whole sentence the farmer reads, with the sources behind it.

    Citation is per block, not per character range: an offset has no defined
    unit across languages, and getting it wrong lands every citation on the
    wrong words in exactly the scripts this system serves.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    source_ids: tuple[str, ...] = ()


class RefusalBlock(BaseModel):
    """Something the DSS will not answer, in words the farmer reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str


OutputContent = TextBlock | RefusalBlock


class TurnOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TurnStatus
    cause: Cause | None = None
    retry_after_seconds: int | None = None


class TurnStarted(BaseModel):
    """The turn was accepted and execution began."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Claim(BaseModel):
    """One reviewed block, ready to present. Emitted as it is produced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: OutputContent


class TurnFinished(BaseModel):
    """The whole turn. Authoritative — a caller stores this rather than
    reassembling the claims it already rendered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: TurnOutcome
    content: tuple[OutputContent, ...] = ()
    sources: tuple[Source, ...] = ()


TurnEvent = TurnStarted | Claim | TurnFinished
