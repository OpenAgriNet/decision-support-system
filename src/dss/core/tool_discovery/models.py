"""The tool-discovery contract (design v2 §5, §6.5). Data shapes only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Tool(BaseModel):
    """One MCP tool as the index knows it. ``description`` is what the planner
    routes on (CONVENTIONS.md: one plain sentence stating what it returns)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str  # "soil-lookup"
    namespace: str  # "acme"
    description: str
    server_id: str  # which MCP server exposes it


class ToolCandidates(BaseModel):
    """Candidate tools per ask, keyed by the ask's position in ``intent.asks``.
    The planner decides which, if any, a step uses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    by_ask: dict[int, tuple[Tool, ...]] = {}
