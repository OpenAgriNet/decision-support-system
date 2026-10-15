"""Tier 3 — the client's ranking is applied where discovery is composed.

The wrapper in `orchestration/discovery.py` is the one place a ranking is
applied (ADR-0019). Everything downstream — the no-match check, the planner's
candidates, `describe_capability` — reads the ranked result and knows nothing
about clients, so these tests pin that it really is ranked on the way out.

Ordering itself is tier 1. These are about the wiring: that the turn's client
is the one looked up, and that what was dropped is said out loud.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.shared.models import Geometry, UserTurn
from dss.orchestration.discovery import build_discover_providers
from tests.support.fakes import FakeProviderPreferences

WEATHER = "openagrinet:WeatherObservation"


class _FakeIndex:
    def current(self):
        return {("Weather", "Service"): (WEATHER,)}


def _capability(provider: str) -> ProviderCapability:
    return ProviderCapability(
        provider_id=provider,
        provider_name=provider.title(),
        capability=WEATHER,
        resource_id=f"res:{provider}:forecast",
        observed_categories=("Weather",),
    )


def _answer(provider: str) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id=provider,
        provider_name=provider.title(),
        capability=WEATHER,
        resource_id=f"res:{provider}:now",
        attributes={},
        validity=None,
    )


class _FakeDiscovery:
    """Every ask finds the same three providers, in the network's own order."""

    async def discover(self, query, ask_indices, transaction_id) -> DiscoveryResult:
        index = ask_indices[0]
        return DiscoveryResult(
            answers={index: (_answer("unvetted"),)},
            capabilities={
                index: (
                    _capability("unvetted"),
                    _capability("mausamgram"),
                    _capability("maharashtra-vistaar"),
                )
            },
            failures={index: ()},
            events=(),
        )


def _turn(client_id: str | None) -> UserTurn:
    return UserTurn(
        original_query="will it rain in nashik",
        enriched_query="will it rain in nashik",
        transaction_id="txn-1",
        session_id="s-1",
        source_lang="en",
        target_lang="en",
        channel="web",
        client_id=client_id,
    )


def _intent() -> Intent:
    return Intent(
        asks=(
            Ask(
                subject_categories=SubjectCategory.WEATHER,
                interaction_type=InteractionType.OBSERVE,
                place=ResolvedPlace(
                    name="Nashik",
                    within=("Maharashtra",),
                    geometry=Geometry(coordinates=[73.79, 19.99]),
                    source=PlaceSource.NAMED,
                ),
            ),
        ),
        confidence=1.0,
    )


def _discover(preferences: FakeProviderPreferences):
    return build_discover_providers(_FakeDiscovery(), _FakeIndex(), 25_000, preferences)


async def test_the_turns_own_client_is_the_one_looked_up() -> None:
    preferences = FakeProviderPreferences(
        {"kisan-mitra-web": {"Weather": ["maharashtra-vistaar"]}}
    )

    await _discover(preferences)(
        _intent(), _turn("kisan-mitra-web"), now=datetime.now(UTC)
    )

    assert preferences.asked == ["kisan-mitra-web"]


async def test_discovery_hands_on_a_ranked_candidate_list() -> None:
    preferences = FakeProviderPreferences(
        {"kisan-mitra-web": {"Weather": ["maharashtra-vistaar", "mausamgram"]}}
    )

    result = await _discover(preferences)(
        _intent(), _turn("kisan-mitra-web"), now=datetime.now(UTC)
    )

    assert [c.provider_id for c in result.capabilities[0]] == [
        "maharashtra-vistaar",
        "mausamgram",
    ]
    # The Direct answer came from a provider this client ruled out.
    assert result.answers[0] == ()


async def test_a_caller_that_named_nobody_gets_the_default_ranking() -> None:
    preferences = FakeProviderPreferences(
        {"kisan-mitra-web": {"Weather": ["maharashtra-vistaar"]}},
        default={"Weather": ["mausamgram"]},
    )

    result = await _discover(preferences)(_intent(), _turn(None), now=datetime.now(UTC))

    assert [c.provider_id for c in result.capabilities[0]] == ["mausamgram"]


async def test_an_unconfigured_deployment_keeps_every_provider() -> None:
    result = await _discover(FakeProviderPreferences())(
        _intent(), _turn("kisan-mitra-web"), now=datetime.now(UTC)
    )

    assert [c.provider_id for c in result.capabilities[0]] == [
        "unvetted",
        "mausamgram",
        "maharashtra-vistaar",
    ]


async def test_what_the_ranking_dropped_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first thing to look at when one client's answers come back thinner
    than another's for the same question. Ids and topics only — never the
    farmer's words."""

    preferences = FakeProviderPreferences(
        {"kisan-mitra-web": {"Weather": ["maharashtra-vistaar"]}}
    )

    with caplog.at_level("INFO", logger="dss.trace"):
        await _discover(preferences)(
            _intent(), _turn("kisan-mitra-web"), now=datetime.now(UTC)
        )

    line = next(
        record.message
        for record in caplog.records
        if "event=ranking_applied" in record.message
    )
    assert "client_id=kisan-mitra-web" in line
    assert "topic=Weather" in line
    assert "dropped=unvetted,mausamgram" in line
    assert "nashik" not in line.lower()
