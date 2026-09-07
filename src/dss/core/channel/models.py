"""The channel-response contract (design v2 §6.10). Data shapes only."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dss.core.composition.models import Confidence
from dss.core.execution.models import Source
from dss.core.shared.models import Refused


class Status(StrEnum):
    """How a turn ended. Only ``ANSWERED`` streams; the rest send one message and
    stop (design v2 §3)."""

    ANSWERED = "answered"
    REJECTED = "rejected"
    NO_MATCH = "no_match"
    NEEDS_CLARIFICATION = "needs_clarification"
    ERROR = "error"


class ChannelChunk(BaseModel):
    """One piece of the response, already shaped for the channel and ready to send."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    is_final: bool = False


class TerminalEvent(BaseModel):
    """The single terminal event that closes a turn's stream, carrying the whole
    answer plus the metadata the caller records against ``trace_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Status
    text: str
    trace_id: str
    cause: str | None = None
    sources: tuple[Source, ...] = ()
    confidence: Confidence = Confidence.LOW
    limitations: tuple[str, ...] = ()
    refused: tuple[Refused, ...] = ()
