"""The review contract (design v2 §6.9). Data shapes only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_index: int
    kind: str  # "ungrounded" | "unsafe" | "too_long" | "tone"
    detail: str


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    grounded: bool
    violations: tuple[Violation, ...] = ()
