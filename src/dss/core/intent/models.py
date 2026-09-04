"""What intent recognition produces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    """What the turn is asking to happen."""

    LOOKUP = "lookup"
    ADVISORY = "advisory"
    TRANSACTION = "transaction"


class Intent(BaseModel):
    """The structured capability need read off a turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_domain: str
    action_type: ActionType
    confidence: float = Field(ge=0.0, le=1.0)
    secondary_domains: tuple[str, ...] = ()
    entities: dict[str, str] = Field(default_factory=dict)
