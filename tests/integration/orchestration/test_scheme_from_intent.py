"""Tier 3 — the scheme catalog wired into the turn (ADR-0021).

Real ``run_turn``, faked ports below it. Proves *placement*: the catalog
reaches the intent prompt, and the scheme intent names is what discovery is
handed. The code check itself is tier 1.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dss.core.intent.models import (
    ClassifiedAsk,
    Intent,
    IntentClassification,
    InteractionType,
    Scheme,
    SubjectCategory,
)
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import TextQuery, UserTurn
from dss.orchestration.turn import run_turn
from tests.support.fakes import FakeAreaLookup, FakePromptProvider, FakeSchemeCatalog

PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")


def _turn(query: str) -> UserTurn:
    return UserTurn(
        input=TextQuery(text=query),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=[],
    )


def _grievance(scheme_code: str | None) -> IntentClassification:
    return IntentClassification(
        asks=(
            ClassifiedAsk(
                agriculture_subjects="fasal bima",
                subject_categories=SubjectCategory.GRIEVANCE,
                interaction_type=InteractionType.ACT,
                scheme_code=scheme_code,
            ),
        ),
        confidence=0.9,
    )


class _FakeLLM:
    def __init__(self, result) -> None:
        self._result = result

    async def structured(self, *, system_prompt, user_query, schema):
        # One fake for both components; each asks for a different schema.
        if schema is IntentClassification:
            return self._result
        return schema()


class _RecordingDiscovery:
    """Captures the intent discovery was actually handed."""

    def __init__(self) -> None:
        self.seen: Intent | None = None

    async def __call__(self, intent: Intent, turn: UserTurn, *, now: datetime):
        self.seen = intent
        return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


async def _run(classification: IntentClassification, *, catalog):
    discovery = _RecordingDiscovery()
    prompts = FakePromptProvider()
    result = await run_turn(
        _turn("my fasal bima claim was rejected"),
        intent_llm=_FakeLLM(classification),
        moderation_llm=_FakeLLM(classification),
        prompts=prompts,
        policies=[],
        discover_providers=discovery,
        area_lookup=FakeAreaLookup(),
        scheme_catalog=catalog,
        now=datetime.now(UTC),
    )
    return result, discovery, prompts


async def test_the_catalog_reaches_the_intent_prompt() -> None:
    _, _, prompts = await _run(_grievance(None), catalog=FakeSchemeCatalog(PMFBY))

    (intent_call,) = [call for call in prompts.calls if call[0].value == "intent"]
    assert [entry.scheme for entry in intent_call[2]["schemes"]] == [PMFBY]


async def test_discovery_receives_the_scheme_intent_named() -> None:
    result, discovery, _ = await _run(
        _grievance("PMFBY"), catalog=FakeSchemeCatalog(PMFBY)
    )

    assert discovery.seen is not None
    (ask,) = discovery.seen.asks
    assert ask.scheme == PMFBY
    assert ask.agriculture_subjects == PMFBY.name
    # The model's category stands; nothing after intent changes it.
    assert ask.subject_categories is SubjectCategory.GRIEVANCE
    assert result.intent.asks[0].scheme == PMFBY


async def test_no_catalog_means_no_scheme_and_the_turn_still_runs() -> None:
    """The unmounted-catalog deployment."""

    result, discovery, prompts = await _run(_grievance("PMFBY"), catalog=None)

    assert discovery.seen.asks[0].scheme is None
    assert discovery.seen.asks[0].agriculture_subjects == "fasal bima"
    (intent_call,) = [call for call in prompts.calls if call[0].value == "intent"]
    assert intent_call[2]["schemes"] == ()
