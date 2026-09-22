"""The six stages a turn passes through.

The names were already a contract before they were a type: `trace_component`
builds `dss.stage.<name>` from them, and the metrics story labels
`dss.stage.duration` with the same string. A dashboard joins the two by that
value, so a typo at one call site silently splits a graph in half.

ADR-0012 recorded "span names are strings at call sites, not one table" as a
cost it accepted. This is the table.
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """One step of a turn. The value is the span suffix and the metric label."""

    INTENT = "intent"
    ENRICHMENT = "enrichment"
    MODERATION = "moderation"
    DISCOVERY = "discovery"
    PLANNER = "planner"
    COMPOSER = "composer"


# The four stages that run a model (ADR-0004 — each binds its own). The other
# two run none, so `dss.stage.duration` carries no `model` label for them: an
# absent label reads as "not applicable", where a "none" value would read as a
# model name in a dashboard's dropdown.
MODEL_BACKED_STAGES = frozenset(
    {Stage.INTENT, Stage.MODERATION, Stage.PLANNER, Stage.COMPOSER}
)
