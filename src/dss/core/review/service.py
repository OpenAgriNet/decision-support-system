"""Response review behaviour (design v2 §6.9).

PLACEHOLDER. A real reviewer checks each claim against its own cited source for
grounding, safety, length and tone. This stand-in passes everything — it exists so
the orchestrator's optional-reviewer wiring (bind → run and log; unbound → skip)
can be exercised before the real check lands. It must never block the stream.
"""

from __future__ import annotations

from dss.core.composition.models import Answer
from dss.core.execution.models import Evidence
from dss.core.review.models import ReviewVerdict


async def review(answer: Answer, evidence: Evidence) -> ReviewVerdict:
    return ReviewVerdict(grounded=True, violations=())
