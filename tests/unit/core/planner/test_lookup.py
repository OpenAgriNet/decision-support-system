"""Tier 1 — finding the ProviderCapability the model picked by resource_id."""

from __future__ import annotations

from dss.core.planner.lookup import find_capability
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability


def _capability(resource_id: str) -> ProviderCapability:
    return ProviderCapability(
        provider_id="agmarknet",
        provider_name="Agmarknet",
        capability="openagrinet:MandiPrice",
        resource_id=resource_id,
        observed_categories=("Market",),
    )


def test_finds_the_matching_capability_for_the_ask() -> None:
    discovery = DiscoveryResult(
        answers={},
        capabilities={0: (_capability("res:a"), _capability("res:b"))},
        failures={},
        events=(),
    )

    result = find_capability(discovery, ask_index=0, resource_id="res:b")

    assert result is not None
    assert result.resource_id == "res:b"


def test_returns_none_when_resource_id_does_not_match_any_candidate() -> None:
    discovery = DiscoveryResult(
        answers={},
        capabilities={0: (_capability("res:a"),)},
        failures={},
        events=(),
    )

    result = find_capability(discovery, ask_index=0, resource_id="res:not-there")

    assert result is None


def test_returns_none_when_the_ask_index_has_no_candidates() -> None:
    discovery = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())

    result = find_capability(discovery, ask_index=5, resource_id="res:a")

    assert result is None
