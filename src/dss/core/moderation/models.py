"""What moderation produces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dss.core.shared.models import Cause


class Outcome(StrEnum):
    """How a turn leaves a checkpoint.

    Not moderation-specific — a policy at any checkpoint, and the planner, can
    land on one of these. The runner decides which path the turn took, because
    only it sees the whole turn.
    """

    PROCEED = "proceed"
    REJECT = "reject"
    CLARIFY = "clarify"
    NO_MATCH = "no_match"


class Screening(BaseModel):
    """The verdict, and the words the farmer reads if it is not `PROCEED`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: Outcome
    cause: Cause | None = None
    message: str = ""
