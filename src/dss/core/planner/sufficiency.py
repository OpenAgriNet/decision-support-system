"""Which asks the turn never answered.

``Evidence.sufficient`` says only whether anything came back. That cannot
tell a model that succeeded from one that gave up: ``failed`` records calls
that errored, not asks the model never attempted, and both leave an ask with
no result. So this compares ``Evidence.served`` against ``Intent.asks``
directly. Plain code, no LLM — a self-graded boolean would say ``True``.

Its own module because it moves: the intended end state is a real
sufficiency judgement in the Response Composer, and this is the shape that
judgement reads.
"""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.planner.models import Evidence


def unserved_asks(evidence: Evidence, *, intent: Intent) -> tuple[int, ...]:
    """The index of every ask that no result served, in ask order."""

    served = set(evidence.served)
    return tuple(index for index in range(len(intent.asks)) if index not in served)
