"""Evidence contract for the Planner Agent — design doc §7."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import anyio
from pydantic import BaseModel, ConfigDict

from dss.core.forms.models import FormDefinition
from dss.core.intent.models import Ask
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
        assert self._decision is not None  # noqa: S101 - type narrowing only
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
    — the position into ``Intent.asks`` — so ``assemble_evidence`` can file
    it under its ask. No ``url`` here: it lives on the ``Source`` this
    result's ``source_id`` points to.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ask_index: int
    source_id: str
    data: dict


class Failure(BaseModel):
    """An ask that did not produce a result.

    ``ask_index`` names what the call was answering — the position into
    ``Intent.asks`` — the same way ``Result.ask_index`` does, so
    ``assemble_evidence`` can file it under its ask.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ask_index: int
    # None: no provider was called, e.g. the ask's place failed to resolve.
    capability: str | None
    reason: str
    retryable: bool

    @property
    def is_provider_failure(self) -> bool:
        return self.capability is not None


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


@dataclass(frozen=True)
class AskEvidence:
    """What the turn gathered for one ask: its results and its failures, or
    the form the farmer fills in before any provider is called."""

    ask: Ask
    results: tuple[Result, ...]
    failures: tuple[Failure, ...]
    awaiting: FormDefinition | None = None

    @property
    def is_served(self) -> bool:
        return bool(self.results)

    @property
    def has_provider_failure(self) -> bool:
        return any(failure.is_provider_failure for failure in self.failures)


@dataclass(frozen=True)
class Evidence:
    """What the loop gathered for a turn: every source it cites, and per ask,
    every result and every gap (design doc §8, open item 14).

    ``sources`` spans the turn, numbered in first-seen order, because one
    source can answer several asks and a claim cites it by that one number.
    ``ask_evidences`` holds one entry per ``Intent.asks``, in ask order.
    """

    sources: tuple[Source, ...]
    ask_evidences: tuple[AskEvidence, ...]

    @property
    def unserved_asks(self) -> tuple[AskEvidence, ...]:
        """Asks with no result and no form: the gaps the farmer is told about."""

        return tuple(
            entry
            for entry in self.ask_evidences
            if not entry.is_served and entry.awaiting is None
        )

    @property
    def awaiting_forms(self) -> tuple[FormDefinition, ...]:
        """In ask order: the reply carries one form block per entry."""

        return tuple(entry.awaiting for entry in self.ask_evidences if entry.awaiting)

    @property
    def has_any_result(self) -> bool:
        return any(entry.is_served for entry in self.ask_evidences)

    @property
    def has_provider_failure(self) -> bool:
        """A place failure called no provider, so it is not an outage and must
        not tell the farmer to retry."""

        return any(entry.has_provider_failure for entry in self.ask_evidences)
