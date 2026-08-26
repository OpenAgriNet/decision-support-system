"""Policy models — the declarative rules moderation evaluates (spec 0003).

This is the *focused* slice: only the two policy shapes this change needs. A
deterministic ``WordCheckPolicy`` (strip listed words, warn, proceed) and an
``LlmPolicy`` (batched judgment, act on ``on_violation``). The full spec's generic
``Condition``/operator engine, merge/precedence, and the other four default
policies are deferred until a policy needs them.

A discriminated union keyed on ``evaluation`` rather than one class with a
validator: the two shapes share almost no fields, so one class would be half-empty
whichever kind it is.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dss.core.moderation.models import Outcome


class Checkpoint(StrEnum):
    """Where a policy fires. Only ``MODERATION`` is evaluated in this slice; the
    other two are declared so an unknown ``checkpoint:`` in YAML is an ordinary
    Pydantic error and future checkpoints need no enum change."""

    MODERATION = "moderation"
    PRE_TOOL_CALL = "pre_tool_call"
    POST_RESPONSE = "post_response"


class EvaluationKind(StrEnum):
    DETERMINISTIC = "deterministic"  # plain Python over the context
    LLM = "llm"  # batched into one judgment call


class FailMode(StrEnum):
    CLOSED = "closed"  # on error, reject the turn (default)
    OPEN = "open"  # on error, let the turn proceed


class PolicyExample(BaseModel):
    """A worked case. Renders into the LLM prompt and doubles as a test fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    expect: Outcome


class PolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str  # kebab-case; "<tenant>/<id>" if adopter-authored
    checkpoint: Checkpoint = Checkpoint.MODERATION
    description: str
    fail_mode: FailMode = FailMode.CLOSED


class WordCheckPolicy(PolicyBase):
    """Deterministic. When a listed word appears in the query it is stripped and a
    warning is raised; the cleaned query proceeds. This does not reject — it is the
    redact-and-warn behaviour of ADR-0002, so it declares no ``on_violation``."""

    evaluation: Literal[EvaluationKind.DETERMINISTIC]
    words: list[str] = Field(min_length=1)
    warning: str  # streamed to the user when a word is stripped


class LlmPolicy(PolicyBase):
    """LLM-evaluated. ``signals`` and ``examples`` render into the batched prompt;
    ``on_violation`` is applied when the model reports this policy fired."""

    evaluation: Literal[EvaluationKind.LLM]
    on_violation: Outcome
    signals: list[str] = Field(min_length=1)
    examples: list[PolicyExample] = Field(default_factory=list)


Policy = Annotated[
    WordCheckPolicy | LlmPolicy,
    Field(discriminator="evaluation"),
]


class PolicyPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    policies: list[Policy]

    @model_validator(mode="after")
    def _ids_unique(self) -> PolicyPack:
        ids = [p.id for p in self.policies]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate policy ids: {dupes}")
        return self

    def for_checkpoint(self, checkpoint: Checkpoint) -> list[Policy]:
        return [p for p in self.policies if p.checkpoint == checkpoint]
