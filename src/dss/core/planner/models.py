"""Evidence contract for the Planner Agent — design doc §7."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SourceKind(StrEnum):
    """What kind of thing a ``Source`` is — the design doc's bare string union."""

    PROVIDER = "provider"
    DOCUMENT = "document"
    TOOL = "tool"


class Source(BaseModel):
    """A citable origin for a claim. ``id`` is the short number a claim cites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    kind: SourceKind
    url: str | None


class Skill(BaseModel):
    """What tools bind for this turn, and the guidance that goes with them
    (ADR-0006 — adds ``description`` and ``tool_names`` to the design doc's shape).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    domain: str
    description: str
    guidance: str
    tool_names: tuple[str, ...]


class Result(BaseModel):
    """One capability call's data, mapped to the domain schema.

    The design doc's ``Result.step_id`` names a ``Plan`` step, which this POC
    does not build (issue #10 scope). ``ask_index`` names what a result
    actually answers here — the position into ``Intent.asks`` — so sufficiency
    can compare ``Evidence.served`` against it directly. No ``url`` here: it
    lives on the ``Source`` this result's ``source_id`` points to.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ask_index: int
    source_id: str
    data: dict


class Failure(BaseModel):
    """A capability call that did not produce a result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str
    reason: str
    retryable: bool


class Identity(BaseModel):
    """Who the assistant is (design doc §6.7). One per deployment, loaded from
    config like ``PolicyPack`` is — injected into the planner prompt for now
    because the planner is what speaks; moves to the Response Composer once
    that component exists (plan issue #10).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    persona: str
    boundaries: str


class Evidence(BaseModel):
    """What the loop gathered for a turn — every source, every result, every
    gap. ``served`` indexes into ``Intent.asks``; ``sufficiency.py`` sets
    ``sufficient`` by comparing the two (design doc §8, open item 14).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[Source, ...]
    results: tuple[Result, ...]
    served: tuple[int, ...]
    failed: tuple[Failure, ...]
    sufficient: bool
