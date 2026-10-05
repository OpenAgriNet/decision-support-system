"""What redaction means, whoever identifies the PII.

An identifier — regex, a name model, an HTTP service — reports ``PiiSpan``s. The
``RedactionPolicy`` says, per entity, whether the real value is kept for the
one provider call that may need it, or destroyed. Neither knows how a span was
found; that is the adapters' business (``adapters/pii_identifier/``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# An entity name becomes part of a tag, «phone_1», so it must be tag-safe.
ENTITY_PATTERN = r"^[a-z][a-z0-9_]*$"
EntityName = Annotated[str, Field(pattern=ENTITY_PATTERN)]


class ValueHandling(StrEnum):
    KEEP = "keep"  # held for this turn so a provider can be sent it
    DESTROY = "destroy"  # replaced and never held — Aadhaar, card


class RedactionPolicy(BaseModel):
    """Per entity: keep or destroy. An entity an identifier reports that is not
    listed here is destroyed — the safe default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entities: dict[EntityName, ValueHandling] = Field(min_length=1)

    def keeps(self, entity: str) -> bool:
        return self.entities.get(entity) is ValueHandling.KEEP


@dataclass(frozen=True, slots=True)
class PiiSpan:
    """A span an identifier says holds PII. Offsets are into the text as the
    farmer wrote it.

    ``value`` is the real value written the one way a provider would be sent it
    (``9876543210``, not ``98765 43210``). The policy decides whether it is kept.
    ``score`` is 1.0 for a pattern; a model reports its own confidence."""

    start: int
    end: int
    entity: str
    score: float
    source: str
    value: str
