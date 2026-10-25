"""The request envelope — the normalized turn the DSS receives (spec 0002, §5.1).

The ``input`` is its own type so a turn can carry more kinds than text, and a
module can declare the kind it handles.

The conversation ``history``, ``location``, and the actor's ``reference`` token
are typed here because the Experience-API envelope carries them and the
HTTP adapter (``adapters/http/v1/mapping.py``) normalizes an inbound
request into this shape.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from dss.core.shared.language import LanguageTag
from dss.core.shared.network import NetworkTransactionID


class TurnInput(BaseModel):
    """What the farmer sent this turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TextQuery(TurnInput):
    text: str


class FormContext(BaseModel):
    """The form's type. Sent with the form, copied back with the submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: str
    category: str
    action: str

    # Pydantic adds the same hash to frozen models; declared so a type checker
    # sees a context can be a dict key.
    def __hash__(self) -> int:
        return hash((self.scheme, self.category, self.action))


class FormReference(BaseModel):
    """What history keeps of a form: its id and type, never its fields or
    values. ``submission_id`` is None for the offer itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    form_id: str
    context: FormContext
    submission_id: str | None = None


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
    # Empty when the message carried only a form: a submission has no words.
    text: str = ""
    form: FormReference | None = None


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


class Refused(BaseModel):
    """One thing the DSS will not answer, and why (design v2 §6.2).

    The partial case: "price of potato and gold" proceeds for potato and refuses
    gold. Moderation produces these; the planner carries them through unchanged
    and the composer says so in the answer. Shared because three components touch
    it and none owns the other's module.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    what: str  # "gold prices"
    reason: str  # "outside agriculture"


class UserTurn[InputT: TurnInput](BaseModel):
    """One normalized turn from the Experience API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Without SerializeAsAny a turn typed by the base serialises its input as
    # an empty TurnInput.
    input: SerializeAsAny[InputT]
    session_id: str
    transaction_id: NetworkTransactionID
    source_lang: LanguageTag  # language the user spoke/typed
    target_lang: LanguageTag  # language the response should come back in
    channel: str  # web / whatsapp / voice / ... (lowercase)
    # Which experience-layer application sent the turn, from the request
    # context's `senderId` field (ADR-0020). Names the caller, never the
    # farmer: it selects whose provider ranking applies. Optional — a caller
    # that sends none is served by the default ranking, so an older
    # experience layer keeps working.
    sender_id: str | None = None
    user: UserDetails = Field(default_factory=UserDetails)
    history: list[ConversationMessage] = Field(default_factory=list)
    location: Location | None = None
    response_max_chars: int | None = None

    def is_target_lang_within(self, broader: LanguageTag) -> bool:
        return self.target_lang.is_within(broader)


# ---------------------------------------------------------------------------
# What a turn produces. Added by the /v1/turns transport (spec 87); main has no
# response types, so nothing above this line changes.
# ---------------------------------------------------------------------------


class TurnContext(BaseModel):
    """The ids a *response* is filed under, and the evidence key.

    `trace_id` is the caller's `transactionId`, echoed back as `traceId`.
    `message_id` is the caller's, or one the transport minted — the contract
    requires it on every response. `session_id` is copied from the turn because
    every frame carries it; `transaction_id` is deliberately absent, since
    `trace_id` already is it. `sender_id` is the immediate caller's network
    participant id; the host network when the caller sends none.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: NetworkTransactionID
    message_id: str
    session_id: str
    sender_id: str


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
    """One whole sentence the farmer reads, with the sources behind it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    source_ids: tuple[str, ...] = ()


class RefusalBlock(BaseModel):
    """Something the DSS will not answer, in words the farmer reads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str


OutputContent = TextBlock | RefusalBlock


class TurnOutcome(BaseModel):
    """How the turn ended, and how sure the DSS is of it.

    `confidence` is on the wire because the contract requires it. What the
    number *means* per status is an open question — a refusal's 98 and an
    answer's 92 are not the same measurement.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: TurnStatus
    confidence: int = Field(ge=0, le=100)
    cause: Cause | None = None
    retry_after_seconds: int | None = None


class TurnStarted(BaseModel):
    """The turn was accepted and execution began."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ClaimDelta(BaseModel):
    """A piece of a claim, handed on as the composer writes it.

    Not a claim. It carries no sources and no citations: mid-write the block has
    no end yet, so a citation over it would span a moving target. Provenance
    arrives with the `Claim` that follows, which is the first point the block is
    final.

    Pieces land on whatever boundary the model produced — mid-word, mid-number,
    mid-citation-marker. Concatenating every delta of a turn gives the `Claim`'s
    text exactly; nothing in the pipeline re-splits them, because that guarantee
    is what lets a caller render them as they arrive.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str


class Claim(BaseModel):
    """One reviewed block, ready to present. Emitted as it is produced.

    ``sources`` is what the block's citations resolve against — carried here
    because a claim is streamed before the terminal frame, so a caller has
    nothing to join a bare id to yet.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: OutputContent
    sources: tuple[Source, ...] = ()


class TurnFinished(BaseModel):
    """The whole turn. Authoritative — a caller stores this rather than
    reassembling the claims it already rendered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: TurnOutcome
    content: tuple[OutputContent, ...] = ()
    sources: tuple[Source, ...] = ()


TurnEvent = TurnStarted | ClaimDelta | Claim | TurnFinished
