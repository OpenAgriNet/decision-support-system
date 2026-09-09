"""Tests for the schema pack cache — build once, refresh on demand."""

from __future__ import annotations

import pytest

from dss.core.provider_discovery import schema_pack_cache
from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache

MANDI_PRICE_ATTRIBUTES = """
components:
  schemas:
    MandiPrice:
      type: object
      x-jsonld:
        "@context": "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
        "@type": openagrinet:MandiPrice
"""

MANDI_PRICE_EXAMPLE = '{"subjectCategories": ["Market"]}'

MANDI_PRICE_PACK = SchemaPackFiles(
    pack_name="MandiPrice",
    version="v0.1",
    profile_json="{}",
    attributes_yaml=MANDI_PRICE_ATTRIBUTES,
    examples_json=(MANDI_PRICE_EXAMPLE,),
)


class _FakeSource:
    def __init__(self, packs=(), *, fail=False, skipped=()):
        self.packs = packs
        self.fail = fail
        self._skipped = skipped

    async def fetch_packs(self):
        if self.fail:
            raise RuntimeError("network-specs unreachable")
        return self.packs

    def skipped_packs(self):
        return self._skipped


async def test_refresh_populates_an_empty_cache() -> None:
    cache = SchemaPackCache(_FakeSource(packs=(MANDI_PRICE_PACK,)))
    assert cache.current() == {}

    await cache.refresh()

    assert cache.current()[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)


async def test_a_failed_refresh_keeps_the_last_good_index() -> None:
    source = _FakeSource(packs=(MANDI_PRICE_PACK,))
    cache = SchemaPackCache(source)
    await cache.refresh()
    good_index = cache.current()

    source.fail = True
    with pytest.raises(RuntimeError):
        await cache.refresh()

    assert cache.current() == good_index


async def test_a_successful_refresh_replaces_the_index_wholesale() -> None:
    source = _FakeSource(packs=(MANDI_PRICE_PACK,))
    cache = SchemaPackCache(source)
    await cache.refresh()

    source.packs = ()
    await cache.refresh()

    assert cache.current() == {}


MARKET_INTELLIGENCE_PACK = SchemaPackFiles(
    pack_name="MarketIntelligence",
    version="v0.1",
    profile_json="{}",
    attributes_yaml="""
components:
  schemas:
    MarketIntelligence:
      type: object
      x-jsonld:
        "@context": "https://schemas.openagrinet.global/schema/MarketIntelligence/v0.1/context.jsonld"
        "@type": openagrinet:MarketIntelligence
""",
    examples_json=('{"subjectCategories": ["Advisory"]}',),
)

MISNAMED_PACK = SchemaPackFiles(
    pack_name="MisnamedPack",
    version="v0.1",
    profile_json="{}",
    attributes_yaml=MANDI_PRICE_ATTRIBUTES,
    examples_json=(MANDI_PRICE_EXAMPLE,),
)


async def test_a_bad_pack_is_skipped_and_exposed_rather_than_raising() -> None:
    """One malformed pack from the external network-specs checkout must not
    blind every other capability — but the skip has to stay visible.
    """
    cache = SchemaPackCache(_FakeSource(packs=(MANDI_PRICE_PACK, MISNAMED_PACK)))

    await cache.refresh()

    assert cache.current()[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)
    assert [s.pack_name for s in cache.skipped_packs()] == ["MisnamedPack"]


async def test_a_clean_refresh_reports_no_skipped_packs() -> None:
    cache = SchemaPackCache(_FakeSource(packs=(MANDI_PRICE_PACK,)))

    await cache.refresh()

    assert cache.skipped_packs() == ()


async def test_a_pack_the_source_itself_could_not_read_is_also_exposed() -> None:
    """A pack can be skipped one layer below index-building — the source
    itself may refuse to read it (two version dirs, a missing file) before
    `build_capability_index` ever sees it. That skip must reach
    `skipped_packs()` too, not disappear because it happened earlier.
    """
    source = _FakeSource(
        packs=(MANDI_PRICE_PACK,),
        skipped=(SchemaPackSkipped("Broken", "expected exactly one version"),),
    )
    cache = SchemaPackCache(source)

    await cache.refresh()

    assert [s.pack_name for s in cache.skipped_packs()] == ["Broken"]


async def test_a_later_clean_refresh_clears_an_earlier_skip() -> None:
    """Skips describe the current index, not history — a fixed pack upstream
    must not leave a stale alert behind.
    """
    source = _FakeSource(packs=(MANDI_PRICE_PACK, MISNAMED_PACK))
    cache = SchemaPackCache(source)
    await cache.refresh()

    source.packs = (MANDI_PRICE_PACK,)
    await cache.refresh()

    assert cache.skipped_packs() == ()


async def test_a_build_that_raises_leaves_both_indexes_untouched(monkeypatch) -> None:
    """The two indexes must never diverge: a @type in the capability index
    with no entry in the schema-context index fails at request-building time.
    Assigning the first index before the second is built is what allows that,
    so the second build is made to raise here.
    """
    source = _FakeSource(packs=(MANDI_PRICE_PACK,))
    cache = SchemaPackCache(source)
    await cache.refresh()
    good_index = cache.current()
    good_context = cache.current_schema_context()

    # The next refresh must see *different* packs, or an index assigned too
    # early would be identical to the last good one and the bug would hide.
    source.packs = (MARKET_INTELLIGENCE_PACK,)

    # Raise on the *final* build, not the skip-collection pass — the bug is
    # about assignment order, so the failure has to land after the first
    # index has been built and could have been assigned.
    real_build = schema_pack_cache.build_schema_context_index
    calls = []

    def _boom_on_the_last_call(packs):
        calls.append(packs)
        if len(calls) > 1:
            raise RuntimeError("schema context build failed")
        return real_build(packs)

    monkeypatch.setattr(
        schema_pack_cache, "build_schema_context_index", _boom_on_the_last_call
    )
    with pytest.raises(RuntimeError):
        await cache.refresh()

    assert cache.current() == good_index
    assert cache.current_schema_context() == good_context


async def test_refresh_also_populates_the_schema_context_index() -> None:
    cache = SchemaPackCache(_FakeSource(packs=(MANDI_PRICE_PACK,)))
    assert cache.current_schema_context() == {}

    await cache.refresh()

    assert (
        cache.current_schema_context()["openagrinet:MandiPrice"]
        == "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
    )
