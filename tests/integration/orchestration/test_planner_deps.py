"""Tier 3 — PlannerDeps, the Agent's deps_type."""

from __future__ import annotations

from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.orchestration.planner import PlannerDeps


def _turn() -> UserTurn:
    return UserTurn(
        original_query="price of potato",
        enriched_query="price of potato",
        transaction_id="txn-1",
        session_id="s-1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


def _discovery_result() -> DiscoveryResult:
    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


def test_planner_deps_starts_with_an_empty_accumulator() -> None:
    deps = PlannerDeps(
        turn=_turn(),
        discovery=_discovery_result(),
        schemas={},
        schema_context_index={},
        schema_base_url="https://schemas.openagrinet.global/schema",
        invocation=None,
    )

    assert deps.raw_answers == []
