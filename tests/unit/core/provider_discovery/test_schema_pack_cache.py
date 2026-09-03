"""Tests for the schema pack cache — build once, refresh on demand."""

from __future__ import annotations

import pytest

from dss.core.provider_discovery.models import SchemaPackFiles
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache

MANDI_PRICE_ATTRIBUTES = """
components:
  schemas:
    MandiPrice:
      allOf:
        - type: object
          properties:
            "@type":
              const: openagrinet:MandiPrice
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
    def __init__(self, packs=(), *, fail=False):
        self.packs = packs
        self.fail = fail

    async def fetch_packs(self):
        if self.fail:
            raise RuntimeError("network-specs unreachable")
        return self.packs


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


async def test_refresh_also_populates_the_schema_context_index() -> None:
    cache = SchemaPackCache(_FakeSource(packs=(MANDI_PRICE_PACK,)))
    assert cache.current_schema_context() == {}

    await cache.refresh()

    assert cache.current_schema_context()["openagrinet:MandiPrice"] == (
        "MandiPrice",
        "v0.1",
    )
