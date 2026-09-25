"""The place-resolution contract.

Shaped like `core/enrichment/models.py`'s `SchemeResolution`: an `Intent` in,
a new `Intent` out with something filled, plus a record of how it went.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from dss.core.intent.models import Intent
from dss.ports.area_lookup import AreaMatch


class PlaceOutcome(StrEnum):
    """How place resolution went for a turn."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    NONE = "none"


class PlaceResolution(BaseModel):
    """The resolved intent and what happened while resolving it.

    `unresolved_name` and `candidates` are only meaningful for `UNRESOLVED`
    and `AMBIGUOUS` respectively — both empty otherwise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    outcome: PlaceOutcome = PlaceOutcome.RESOLVED
    unresolved_name: str | None = None
    candidates: tuple[AreaMatch, ...] = ()
