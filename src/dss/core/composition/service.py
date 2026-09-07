"""Response composition behaviour (design v2 §6.8).

PLACEHOLDER. The real composer is an LLM that renders the evidence into fluent,
grounded prose in ``target_lang`` using the persona and skills, streaming claims
as they are ready. This stand-in turns each result into one mechanical claim so
the workflow — and the channel shaping after it — has something real to carry.

It already honours the three composer rules that are contract, not prose:
cites its sources, says what it refused, and says "I don't know" (emits no
grounded claims, ``LOW`` confidence) when the evidence is insufficient.

Returns a whole ``Answer`` rather than streaming ``Claim``s: claim-by-claim
streaming across the boundary is the resumability question left open in the design
(§2, Open #12) and is deferred with it.
"""

from __future__ import annotations

from dss.core.composition.models import Answer, Claim, Confidence, Identity
from dss.core.execution.models import Evidence
from dss.core.planning.models import Plan


async def compose(evidence: Evidence, plan: Plan, identity: Identity) -> Answer:
    claims: list[Claim] = []

    if evidence.sufficient:
        for result in evidence.results:
            claims.append(
                Claim(
                    text=f"{result.data}",  # placeholder rendering
                    source_id=result.source_id,
                )
            )
    else:
        claims.append(Claim(text="I don't have enough to answer that.", source_id=None))

    # Say what we refused (design v2 §6.8) — an uncited connective sentence.
    for refused in plan.refused:
        claims.append(Claim(text=f"I cannot help with {refused.what}.", source_id=None))

    return Answer(
        claims=tuple(claims),
        sources=evidence.sources,
        confidence=Confidence.HIGH if evidence.sufficient else Confidence.LOW,
        refused=plan.refused,
        limitations=(),
    )
