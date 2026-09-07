"""Plan execution behaviour (design v2 §6.7).

PLACEHOLDER. The real executioner is the only component that touches the outside
world — it walks the plan graph (``depends_on``, ``inputs``, ``on_empty``), calls
MCP tools and Provider ``select`` through the invocation port, and maps each
result onto the domain schema. None of that is wired yet, so this stand-in
synthesizes one echoed ``Result`` per step: enough for the composer and channel to
run, but it fetches nothing real.

When the real executioner lands it takes the same ``Plan`` in and returns the same
``Evidence`` out — only this module changes.
"""

from __future__ import annotations

from dss.core.execution.models import Evidence, Result, Source
from dss.core.planning.models import Plan


async def execute(plan: Plan) -> Evidence:
    sources: list[Source] = []
    results: list[Result] = []
    for step in plan.steps:
        source_id = str(step.id)
        sources.append(
            Source(
                id=source_id,
                name=step.capability,
                kind="provider",
                url=None,
            )
        )
        results.append(
            Result(
                step_id=step.id,
                source_id=source_id,
                # Placeholder: echo the plan's own inputs. A real call replaces
                # this with the provider's / tool's mapped response.
                data={"capability": step.capability, "inputs": dict(step.inputs)},
            )
        )
    return Evidence(
        sources=tuple(sources),
        results=tuple(results),
        served=plan.serves,
        failed=(),
        sufficient=bool(results),
    )
