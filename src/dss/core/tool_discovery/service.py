"""Tool discovery behaviour (design v2 §6.5).

Threshold rule (from the design): at or below ``filter_above`` tools, pass them
all to the planner — filtering a short list costs accuracy for no gain. Above it,
narrow per ask by BM25 over the index using the ask's own terms.

PLACEHOLDER for the search path: the ``search`` call is delegated to the injected
``ToolIndex`` port, but no real BM25 adapter is wired yet, so a deployment with an
empty index simply yields no candidates. Local only — no network call.
"""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.tool_discovery.models import Tool, ToolCandidates
from dss.ports.tools import ToolIndex

# Anthropic's own threshold for switching on tool search: 10 tools (design v2).
_FILTER_ABOVE = 10


def _terms(index: int, intent: Intent) -> list[str]:
    ask = intent.asks[index]
    terms = [ask.subject_categories.value, ask.interaction_type.value]
    if ask.agriculture_subjects:
        terms.append(ask.agriculture_subjects)
    return terms


async def discover_tools(
    intent: Intent, index: ToolIndex, filter_above: int = _FILTER_ABOVE
) -> ToolCandidates:
    catalogue = index.all()
    by_ask: dict[int, tuple[Tool, ...]] = {}
    for ask_index in range(len(intent.asks)):
        if len(catalogue) <= filter_above:
            by_ask[ask_index] = catalogue
        else:
            by_ask[ask_index] = index.search(
                _terms(ask_index, intent), limit=filter_above
            )
    return ToolCandidates(by_ask=by_ask)
