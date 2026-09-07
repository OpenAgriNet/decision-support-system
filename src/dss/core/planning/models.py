"""The planning contract (design v2 §6.6). Data shapes only.

The plan is *data*, not code — the executioner reads it. ``steps`` and ``missing``
are independent: a plan may carry steps, missing inputs, or both, and the
orchestrator reads the pair to decide the turn's outcome.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dss.core.shared.models import Refused
from dss.core.skills.models import Skill


class DomainSchema(BaseModel):
    """One network capability's input schema, read from ``network-specs`` — the
    DSS reads these, never authors them (design v2 §6.6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str  # "openagrinet:MandiPriceCapability"
    filterable: tuple[str, ...] = ()  # from profile.json filterable_paths
    required: tuple[str, ...] = ()  # filters that must be supplied


class DomainSchemas(BaseModel):
    """The schemas this turn needs, keyed by capability ``@type``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    by_type: dict[str, DomainSchema] = {}


class MissingInput(BaseModel):
    """An input the plan could not fill — ask the farmer, do not guess. ``name`` is
    the domain schema's filter name (``"market.state"``), shared across every
    provider in that domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class Fallback(BaseModel):
    """A different capability to try for the same ask if a step returns nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str
    inputs: dict[str, str] = {}


class Step(BaseModel):
    """One capability to call. ``inputs`` are filter values; ``"$1.gps"`` reads
    step 1's gps field. ``depends_on`` names steps that must finish first."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    capability: str  # a schema @type — not one named provider
    inputs: dict[str, str] = {}
    depends_on: tuple[int, ...] = ()
    on_empty: Fallback | None = None


class Plan(BaseModel):
    """What to run for a turn. ``serves`` is the index of each ask this plan
    covers; ``refused`` is carried through from moderation; ``missing`` are inputs
    that could not be filled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: tuple[Step, ...] = ()
    skills: tuple[Skill, ...] = ()  # carried through to the composer prompt
    serves: tuple[int, ...] = ()
    refused: tuple[Refused, ...] = ()
    missing: tuple[MissingInput, ...] = ()
