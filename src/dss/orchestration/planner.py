"""The tool-calling agent loop that stands in for the planner/executioner
pair (ADR-0005). ``core/planner/`` does the work; this module wires it to
Pydantic AI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import DiscoveredAnswer, DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.ports.invocation import CapabilityInvocation


@dataclass
class PlannerDeps:
    """Everything the agent's tools need for one turn. A plain object passed
    to ``agent.run(deps=...)`` — tools read it via ``RunContext.deps``."""

    turn: UserTurn
    discovery: DiscoveryResult
    schemas: dict[str, DomainSchema]
    schema_context_index: dict[str, tuple[str, str]]
    schema_base_url: str
    invocation: CapabilityInvocation | None
    raw_answers: list[tuple[int, DiscoveredAnswer]] = field(default_factory=list)
