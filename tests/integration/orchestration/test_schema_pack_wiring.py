"""End-to-end: a path on disk becomes a populated, queryable capability index."""

from __future__ import annotations

from pathlib import Path

from dss.orchestration.schema_packs import build_schema_pack_cache

FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "adapters"
    / "schema_packs"
    / "fixtures"
    / "network-specs"
    / "schema"
)


async def test_the_wired_cache_resolves_a_capability_after_refresh() -> None:
    cache = build_schema_pack_cache(FIXTURE_ROOT)
    assert cache.current() == {}

    await cache.refresh()

    assert cache.current()[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)
