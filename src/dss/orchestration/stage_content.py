"""What each stage read and wrote, as short text for Langfuse.

Plain summaries, one line per ask or source, so a trace reads without opening
the model calls underneath. Writing them onto a span is
`set_current_span_content`'s job, which keeps them behind the content switch.
"""

from __future__ import annotations

from collections.abc import Sequence

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    ClassifiedAsk,
    ResolvedPlace,
    UnresolvedPlace,
)
from dss.core.planner.models import Evidence
from dss.core.provider_discovery.models import DiscoveryResult


def asks_text(asks: Sequence[Ask | ClassifiedAsk]) -> str:
    """`market/observe: wheat @ Pune`, one line per ask."""

    if not asks:
        return "no asks"
    return "\n".join(
        f"{i + 1}. {ask.subject_categories.value}/{ask.interaction_type.value}: "
        f"{ask.agriculture_subjects or '-'}{_place(ask)}"
        for i, ask in enumerate(asks)
    )


def _place(ask: Ask | ClassifiedAsk) -> str:
    if isinstance(ask, ClassifiedAsk):
        name = ask.place_name
    elif isinstance(ask.place, ResolvedPlace):
        name = ask.place.name or "device point"
    elif isinstance(ask.place, AmbiguousPlace | UnresolvedPlace):
        name = ask.place.unresolved_name
    else:
        name = None
    return f" @ {name}" if name else ""


def discovery_text(result: DiscoveryResult) -> str:
    """Per ask: who answered, who offers to, and who failed."""

    asks = sorted(
        result.answers.keys() | result.capabilities.keys() | result.failures.keys()
    )
    if not asks:
        return "nobody found"
    lines = []
    for index in asks:
        parts = []
        if answers := result.answers.get(index):
            parts.append("answered by " + ", ".join(a.provider_name for a in answers))
        if offers := result.capabilities.get(index):
            parts.append("offered by " + ", ".join(c.provider_name for c in offers))
        if failures := result.failures.get(index):
            parts.append(
                "failed: "
                + ", ".join(
                    f"{f.provider_id or '?'} ({f.status_code})" for f in failures
                )
            )
        lines.append(f"{index + 1}. " + ("; ".join(parts) or "nobody"))
    return "\n".join(lines)


def sources_text(evidence: Evidence) -> str:
    """The sources the evidence came from, one line each."""

    if not evidence.sources:
        return "no sources"
    return "\n".join(f"[{s.id}] {s.name}" for s in evidence.sources)


def evidence_text(evidence: Evidence) -> str:
    """Which asks were served, from where, and what failed."""

    served = ", ".join(str(i + 1) for i in evidence.served) or "none"
    lines = [f"served asks: {served}", sources_text(evidence)]
    lines += [
        f"failed ask {f.ask_index + 1}: {f.capability or '-'}: {f.reason}"
        for f in evidence.failed
    ]
    return "\n".join(lines)
