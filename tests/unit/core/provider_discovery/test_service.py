"""Tests for the @type resolver."""

from __future__ import annotations

from dss.core.provider_discovery.models import CapabilityUnresolved
from dss.core.provider_discovery.service import resolve_capability_type


def test_a_pinned_pair_resolves_to_its_single_type() -> None:
    index = {("Market", "Knowledge"): ("MarketIntelligence",)}

    types, event = resolve_capability_type("Market", "Knowledge", index)

    assert types == ("MarketIntelligence",)
    assert event is None


def test_a_pair_missing_from_the_index_resolves_to_no_types() -> None:
    index: dict[tuple[str, str], tuple[str, ...]] = {}

    types, event = resolve_capability_type("Market", "Knowledge", index)

    assert types == ()
    assert event == CapabilityUnresolved("Market", "Knowledge")


def test_an_unpinned_pair_resolves_to_several_types() -> None:
    index = {("Market", "Knowledge"): ("MandiPrice", "MarketIntelligence")}

    types, event = resolve_capability_type("Market", "Knowledge", index)

    assert types == ("MandiPrice", "MarketIntelligence")
    assert event is None
