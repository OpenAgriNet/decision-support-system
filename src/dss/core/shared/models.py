"""The request envelope — the normalized turn the DSS receives (spec 0002, §5.1).

``UserTurn`` carries the ``(original_query, enriched_query)`` pair from the start
(architecture §3.0): moderation evaluates the enriched query, and until
enrichment lands ``enriched_query`` mirrors ``original_query``. Defining both now
means later slices do not have to re-thread the second field through every
signature.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# BCP-47 primary subtag plus optional subtags — enough to reject full names like
# "gujarati" while accepting "gu", "hi", "en", "en-IN" (CONVENTIONS.md).
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")


class UserDetails(BaseModel):
    """Actor identifiers. Both optional — a turn need not be attributed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str | None = None
    phone: str | None = None


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
    history: list = Field(default_factory=list)  # typed shape deferred (§8)
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
