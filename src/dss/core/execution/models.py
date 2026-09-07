"""The execution contract (design v2 §6.7). Data shapes only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Source(BaseModel):
    """A numbered origin a claim can cite."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # "1", "2"
    name: str  # "Agmarknet"
    kind: str  # "provider" | "document" | "tool"
    url: str | None = None


class Result(BaseModel):
    """One step's returned data, mapped to the domain schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: int
    source_id: str
    data: dict[str, object]


class Failure(BaseModel):
    """One step that failed. ``retryable`` distinguishes a transient miss from a
    defect."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: int
    capability: str
    reason: str
    retryable: bool


class Evidence(BaseModel):
    """Everything execution gathered. ``sufficient=False`` means the honest answer
    is "I don't know" — the composer must never invent (design v2 §6.7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[Source, ...] = ()
    results: tuple[Result, ...] = ()
    served: tuple[int, ...] = ()  # index of each ask actually answered
    failed: tuple[Failure, ...] = ()
    sufficient: bool = False
