"""Tier 3 — scheme enrichment wired into the turn.

Real ``run_turn``, faked ports below it. Proves *placement*: enrichment runs
after intent and discovery is handed the resolved subject. The resolver's own
rules are tier 1.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
from dss.orchestration.turn import run_turn
from tests.support.fakes import FakeSchemeCatalog

MAKHANA = "Central Sector Scheme for Development of Makhana"
CATALOG = FakeSchemeCatalog({"makhana scheme": MAKHANA})


def _turn(query: str) -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=[],
    )


def _intent(subject: str, category: SubjectCategory) -> Intent:
    return Intent(
        asks=(
            Ask(
                agriculture_subjects=subject,
                subject_categories=category,
                interaction_type=InteractionType.ADVISE,
            ),
        ),
        confidence=0.9,
    )


class _FakeLLM:
    def __init__(self, result) -> None:
        self._result = result

    async def structured(self, *, system_prompt, user_query, schema):
        # One fake for both components; each asks for a different schema.
        if schema is Intent:
            return self._result
        return schema()


class _RecordingDiscovery:
    """Captures the intent discovery was actually handed."""

    def __init__(self) -> None:
        self.seen: Intent | None = None

    async def __call__(self, intent: Intent, turn: UserTurn, *, now: datetime):
        self.seen = intent
        return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


async def _run(intent: Intent, query: str, *, catalog=CATALOG, **kwargs):
    discovery = _RecordingDiscovery()
    result = await run_turn(
        _turn(query),
        intent_llm=_FakeLLM(intent),
        moderation_llm=_FakeLLM(intent),
        policies=[],
        discover_providers=discovery,
        scheme_catalog=catalog,
        now=datetime.now(UTC),
        **kwargs,
    )
    return result, discovery


async def test_discovery_receives_the_canonical_scheme_name() -> None:
    """The point of the placement: enrichment runs between the classifier and
    discovery, so what routes is the resolved ask, not the raw one."""

    result, discovery = await _run(
        _intent("makhana", SubjectCategory.SCHEME),
        "i want to know about makhana scheme",
    )

    assert discovery.seen is not None
    assert discovery.seen.asks[0].agriculture_subjects == MAKHANA
    assert result.intent.asks[0].agriculture_subjects == MAKHANA


async def test_a_market_ask_reaches_discovery_untouched() -> None:
    result, discovery = await _run(
        _intent("makhana", SubjectCategory.MARKET),
        "makhana scheme price in patna mandi",
    )

    assert discovery.seen.asks[0].agriculture_subjects == "makhana"
    assert result.intent.asks[0].agriculture_subjects == "makhana"


async def test_no_catalog_leaves_the_intent_alone() -> None:
    """The unmounted-catalog deployment: inert, and the turn still runs."""

    result, discovery = await _run(
        _intent("makhana", SubjectCategory.SCHEME),
        "i want to know about makhana scheme",
        catalog=None,
    )

    assert discovery.seen.asks[0].agriculture_subjects == "makhana"
    assert result.intent.asks[0].agriculture_subjects == "makhana"


async def test_a_resolution_is_traced(caplog) -> None:
    """A rewrite that nothing logged would be invisible: the ask that reaches
    discovery no longer holds the farmer's words."""

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        await _run(
            _intent("makhana", SubjectCategory.SCHEME),
            "i want to know about makhana scheme",
        )

    assert "component=enrichment event=scheme_resolved" in caplog.text
    assert "alias=makhana scheme" in caplog.text
    assert "request_id=t1" in caplog.text


async def test_the_enrichment_span_is_traced_even_with_no_match(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="dss.trace"):
        await _run(_intent("wheat", SubjectCategory.MARKET), "wheat price")

    assert "component=enrichment event=enter" in caplog.text
    assert "event=scheme_resolved" not in caplog.text
