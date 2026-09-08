"""Evidence contract for the Planner Agent — design doc §7."""

from __future__ import annotations

from enum import StrEnum

import anyio
from pydantic import BaseModel, ConfigDict

from dss.core.moderation.models import ModerationDecision


class Verdict:
    """The moderation decision, awaited by any tool that leaves the process.

    Mutable, unlike the frozen models around it: the decision arrives after
    construction, because the planner starts before moderation finishes.

    A holder, not an awaitable. The model may call a tool several times in
    one turn, and a coroutine can only be awaited once. An ``anyio.Event``
    can be waited on as often as asked.
    """

    def __init__(self) -> None:
        self._event = anyio.Event()
        self._decision: ModerationDecision | None = None

    def set(self, decision: ModerationDecision) -> None:
        self._decision = decision
        self._event.set()

    def is_set(self) -> bool:
        """Whether the decision has landed. For assertions and logging — a
        tool wanting the decision should ``await get()`` instead."""

        return self._event.is_set()

    async def get(self) -> ModerationDecision:
        """Block until moderation has decided, then return its decision."""

        await self._event.wait()
        assert self._decision is not None
        return self._decision


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

    The design doc's ``Result.step_id`` names a ``Plan`` step; there is no
    ``Plan`` here. ``ask_index`` names what a result actually answers instead
    — the position into ``Intent.asks`` — so sufficiency can compare
    ``Evidence.served`` against it directly. No ``url`` here: it lives on the
    ``Source`` this result's ``source_id`` points to.
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
    that component exists.
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
