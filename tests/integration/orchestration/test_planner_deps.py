"""Tier 3 — PlannerDeps, the Agent's deps_type."""

from __future__ import annotations

from dss.core.intent.models import Intent, InteractionType, SubjectCategory
from dss.core.planner.models import Verdict
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import TextQuery, UserTurn
from dss.orchestration.planner import PlannerDeps
from tests.support.asks import an_ask
from tests.support.discovery import a_discovery


def _turn() -> UserTurn:
    return UserTurn(
        input=TextQuery(text="price of potato"),
        transaction_id="txn-1",
        session_id="s-1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


def _discovery_result() -> DiscoveryResult:
    return a_discovery()


class _UnusedInvocation:
    """These tests never reach ``select``, but ``invocation`` is required —
    the tool cannot work without one, so the type says so."""

    async def select(
        self, capability, resource_attributes, transaction_id, *, visibility=None
    ):
        raise AssertionError("select must not be called in this test")


def _intent(category: SubjectCategory = SubjectCategory.MARKET) -> Intent:
    """One ask at index 0 — the only index these tests' discovery uses."""

    return Intent(
        asks=(
            an_ask(
                agriculture_subjects="paddy",
                subject_categories=category,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=1.0,
    )


def test_planner_deps_starts_with_an_empty_accumulator() -> None:
    deps = PlannerDeps(
        turn=_turn(),
        intent=_intent(),
        discovery=_discovery_result(),
        schemas={},
        schema_context_index={},
        invocation=_UnusedInvocation(),
        verdict=Verdict(),
    )

    assert deps.raw_answers == []
