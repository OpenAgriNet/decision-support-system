"""Tier 3 — ``build_components`` wires the real intent/moderation services and the
placeholder rest into a runnable turn. The ports below (LLM, discovery, tool
index) are faked; everything above them is the production composition.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from dss.core.composition.models import Identity
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.core.tool_discovery.models import Tool
from dss.orchestration.orchestrator import build_components, run_turn

NOW = datetime(2026, 9, 7, tzinfo=UTC)
IDENTITY = Identity(name="Kisan Mitra", persona="helpful", boundaries="agriculture")


class _FakeLLM:
    def __init__(self, result) -> None:
        self._result = result

    async def structured(self, *, system_prompt, user_query, schema):
        return self._result


class _EmptyToolIndex:
    def all(self) -> tuple[Tool, ...]:
        return ()

    def search(self, terms: Sequence[str], limit: int) -> tuple[Tool, ...]:
        return ()

    def refresh(self, server_id: str) -> None:  # pragma: no cover - unused here
        raise NotImplementedError


async def _no_providers(
    intent: Intent, turn: UserTurn, now: datetime
) -> DiscoveryResult:
    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


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


async def test_build_components_runs_a_turn_end_to_end() -> None:
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
        discover_providers=_no_providers,
        tool_index=_EmptyToolIndex(),
        identity=IDENTITY,
    )

    chunks = [chunk async for chunk in run_turn(_turn(), components, now=NOW)]

    # No provider serves the ask → the plan is empty → the no-match path: one
    # terminal message, and no attempt to stream a composed answer.
    assert len(chunks) == 1
    assert chunks[0].is_final
