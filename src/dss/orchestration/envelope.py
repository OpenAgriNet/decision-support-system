"""The Experience-API envelope and its mapping to a ``UserTurn`` (ADR-0004).

The inbound request is an OpenAI-style thread (``input`` — a list of role/content
messages) plus ``user_context`` and ``attributes``. This boundary normalizes it
into the domain ``UserTurn`` the core works with: the last ``user`` message is the
current query, everything before it is history, and the camelCase attributes map
onto the turn's typed fields.

The envelope is the only place camelCase and provider-shaped JSON is allowed —
the core never sees it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dss.core.shared.models import (
    ConversationMessage,
    Location,
    ReferenceToken,
    UserDetails,
    UserTurn,
)


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str


class InputMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    # Canonical form is a list of typed parts; a bare string is accepted for the
    # simpler channels that send one.
    content: str | list[ContentPart]

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content.strip()
        return " ".join(p.text for p in self.content if p.type == "text").strip()


class UserContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str = "anonymous"  # canonical id; "anonymous" if unknown
    reference_token: str | None = None  # token for Provider calls; passed on
    issuer: str | None = None
    expires_at: datetime | None = None


class ResponseAttributes(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_characters: int | None = None


class Attributes(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    source_language: str = Field(alias="sourceLanguage")
    target_language: str = Field(alias="targetLanguage")
    channel: str
    location: Location | None = None
    response: ResponseAttributes | None = None


class TurnEnvelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    input: list[InputMessage] = Field(min_length=1)
    user_context: UserContext = Field(default_factory=UserContext)
    attributes: Attributes


def _reference(ctx: UserContext, now: datetime) -> ReferenceToken | None:
    """Build the reference token, treating an expired one as absent (spec)."""

    if ctx.reference_token is None:
        return None
    if ctx.expires_at is not None and ctx.expires_at <= now:
        return None
    return ReferenceToken(
        value=ctx.reference_token,
        issuer=ctx.issuer,
        expires_at=ctx.expires_at,
    )


def to_user_turn(envelope: TurnEnvelope, *, now: datetime) -> UserTurn:
    """Normalize the envelope into a ``UserTurn``.

    The last ``user`` message is the current query; the messages before it are the
    history. ``now`` decides whether the reference token is still live.
    """

    last_user = next(
        (
            i
            for i in reversed(range(len(envelope.input)))
            if envelope.input[i].role == "user"
        ),
        None,
    )
    if last_user is None:
        raise ValueError("envelope.input has no user message to act on")

    current = envelope.input[last_user].text()
    history = [
        ConversationMessage(role=m.role, text=m.text())
        for m in envelope.input[:last_user]
    ]

    ctx = envelope.user_context
    attrs = envelope.attributes
    max_chars = attrs.response.max_characters if attrs.response else None

    return UserTurn(
        original_query=current,
        enriched_query=current,  # enrichment has not run yet — mirror the raw query
        # The envelope carries no session id; key the turn on the canonical user id.
        session_id=ctx.user_id,
        source_lang=attrs.source_language,
        target_lang=attrs.target_language,
        channel=attrs.channel,
        user=UserDetails(user_id=ctx.user_id, reference=_reference(ctx, now)),
        history=history,
        location=attrs.location,
        response_max_chars=max_chars,
    )
