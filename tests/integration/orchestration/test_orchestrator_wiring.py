"""Tier 3 — ``build_components`` wires the real intent + moderation services and the
planner into a runnable turn. The ports below (LLM, discovery) are faked; everything
above them is the production composition.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import Outcome
from dss.core.provider_discovery.models import (
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.shared.models import UserTurn
from dss.orchestration.orchestrator import build_components, run_turn

NOW = datetime(2026, 9, 8, tzinfo=UTC)


class _FakeLLM:
    def __init__(self, result) -> None:
        self._result = result

    async def structured(self, *, system_prompt, user_query, schema):
        return self._result


async def _one_provider(
    intent: Intent, turn: UserTurn, now: datetime
) -> DiscoveryResult:
    capabilities = {
        i: (
            ProviderCapability(
                provider_id="agmarknet",
                provider_name="Agmarknet",
                capability="openagrinet:MandiPrice",
                resource_id=f"res:{i}",
            ),
        )
        for i in range(len(intent.asks))
    }
    return DiscoveryResult(
        answers={}, capabilities=capabilities, failures={}, events=()
    )


def _turn() -> UserTurn:
    return UserTurn(
        original_query="potato price?",
        enriched_query="potato price?",
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


async def test_build_components_runs_a_turn_to_a_plan() -> None:
    intent = Intent(
        asks=(
            Ask(
                agriculture_subjects="potato",
                subject_categories=SubjectCategory.MARKET,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=0.9,
    )
    components = build_components(
        intent_llm=_FakeLLM(intent),
        moderation_llm=_FakeLLM(None),  # no LLM policies in play → never consulted
        policies=[],
        discover_providers=_one_provider,
    )

    result = await run_turn(_turn(), components, now=NOW)

    assert result.outcome is Outcome.PROCEED
    assert result.plan is not None
    assert result.plan.steps  # the discovered capability became a plan step
