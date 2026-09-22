"""What a turn's model calls report back to the turn.

Two numbers are known only inside a model call but belong to the turn around
it: what the call cost, and which model actually answered. The call sites are
in `adapters/llm/` and `orchestration/`; the readers are the turn span and
`trace_component`. Neither side can hand the other an argument without
threading telemetry through `core/`, so they meet here.

**Why one mutable object rather than a plain value.** Intent, moderation and
discovery run as siblings under one task group, and starting a task copies the
context. A ContextVar holding a number would give each child its own copy and
the parent would drain a cost of zero. A ContextVar holding an object gives
each child the same reference, so a child's `+=` is visible to the parent.

Stdlib only, like the rest of this package — safe to import from any layer.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from dss.observability.stages import Stage


@dataclass
class TurnUsage:
    """One turn's running totals. Mutated in place, never replaced."""

    cost: float = 0.0
    # Keyed by stage, not one slot: intent and moderation run at the same time,
    # so a single slot would label one stage with the other's model.
    models: dict[Stage, str] = field(default_factory=dict)


_turn_usage: ContextVar[TurnUsage | None] = ContextVar("dss_turn_usage", default=None)


def begin_turn_usage() -> TurnUsage:
    """Start a turn's accounting. Called once, where the turn span opens."""

    usage = TurnUsage()
    _turn_usage.set(usage)
    return usage


def clear_turn_usage() -> None:
    """Forget this turn's totals.

    A ContextVar outlives the block that set it, so without this a later call
    with no turn around it would add to the last turn's cost. Production sets a
    fresh one per turn and never sees it; a test that asserts the no-turn
    behaviour does.
    """

    _turn_usage.set(None)


def current_turn_usage() -> TurnUsage | None:
    """This turn's totals, or ``None`` outside a turn.

    ``None`` is the normal state in unit tests and anywhere the pipeline is
    exercised without a turn span, so every writer below tolerates it.
    """

    return _turn_usage.get()


def add_cost(amount: float) -> None:
    """Add one model call's cost. A no-op outside a turn."""

    usage = _turn_usage.get()
    if usage is not None:
        usage.cost += amount


def record_stage_model(stage: Stage, model: str) -> None:
    """Name the model that answered for this stage. A no-op outside a turn."""

    usage = _turn_usage.get()
    if usage is not None:
        usage.models[stage] = model


def model_for(stage: Stage) -> str | None:
    """The model that answered for this stage, if one did.

    ``None`` for enrichment and discovery, which run no model — and for any
    stage whose call has not returned yet.
    """

    usage = _turn_usage.get()
    if usage is None:
        return None
    return usage.models.get(stage)
