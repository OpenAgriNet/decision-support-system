"""The composition contract (design v2 §6.8). Data shapes only."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dss.core.execution.models import Source
from dss.core.shared.models import Refused


class Confidence(StrEnum):
    HIGH = "high"
    LOW = "low"


class Identity(BaseModel):
    """Who the assistant is for this tenant — the Identity config primitive. The
    only place persona enters the pipeline (design v2 §6.8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # "Kisan Mitra"
    persona: str
    boundaries: str


class Claim(BaseModel):
    """One sentence. ``source_id`` is ``None`` for connective sentences."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    source_id: str | None = None


class Answer(BaseModel):
    """The composed answer. Channel-agnostic — the composer never learns which
    channel it served."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: tuple[Claim, ...] = ()
    sources: tuple[Source, ...] = ()
    confidence: Confidence = Confidence.LOW
    refused: tuple[Refused, ...] = ()
    limitations: tuple[str, ...] = ()
