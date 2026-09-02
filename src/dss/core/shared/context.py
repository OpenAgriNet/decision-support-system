"""Per-turn runtime dependencies, injected by the orchestration layer.

``UserTurn`` carries the *data* of a turn; ``TurnContext`` carries the *wiring*
a core service needs to do its work — the LLM port and the trace id for
evidence. Keeping the two apart means a core service signature says exactly what
it consumes: ``classify(turn, taxonomy, ctx)`` takes the data, the config, and
the dependencies as three distinct things.

It holds a Protocol (``LLMProvider``), so it is a frozen dataclass rather than a
pydantic model — a plain dependency container, not validated wire data.
"""

from __future__ import annotations

from dataclasses import dataclass

from dss.ports import LLMProvider


@dataclass(frozen=True, slots=True)
class TurnContext:
    """Dependencies for handling one turn.

    Attributes:
        llm: the model provider a core service calls; a fake in tier-1 tests.
        trace_id: the per-turn, non-personal id that joins work to its evidence
            record (API contract §3.2 ``trace_id``). The DSS mints one when the
            caller omits ``X-Trace-Id``.
    """

    llm: LLMProvider
    trace_id: str
