"""Tests for building the capability index from schema pack files."""

from __future__ import annotations

from dss.core.provider_discovery.index import build_capability_index
from dss.core.provider_discovery.models import SchemaPackFiles

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

MARKET_INTELLIGENCE_ATTRIBUTES = """
components:
  schemas:
    MarketIntelligence:
      allOf:
        - type: object
          properties:
            "@type":
              const: openagrinet:MarketIntelligence
"""

MARKET_INTELLIGENCE_EXAMPLE = '{"subjectCategories": ["Market"]}'


def test_a_single_pack_maps_its_category_to_its_type() -> None:
    pack = SchemaPackFiles(
        pack_name="MandiPrice",
        profile_json="{}",
        attributes_yaml=MANDI_PRICE_ATTRIBUTES,
        examples_json=(MANDI_PRICE_EXAMPLE,),
    )

    index = build_capability_index((pack,))

    assert index[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)
    assert index[("Market", "Service")] == ("openagrinet:MandiPrice",)


def test_two_distinct_packs_sharing_a_category_both_appear() -> None:
    mandi_price = SchemaPackFiles(
        pack_name="MandiPrice",
        profile_json="{}",
        attributes_yaml=MANDI_PRICE_ATTRIBUTES,
        examples_json=(MANDI_PRICE_EXAMPLE,),
    )
    market_intelligence = SchemaPackFiles(
        pack_name="MarketIntelligence",
        profile_json="{}",
        attributes_yaml=MARKET_INTELLIGENCE_ATTRIBUTES,
        examples_json=(MARKET_INTELLIGENCE_EXAMPLE,),
    )

    index = build_capability_index((mandi_price, market_intelligence))

    assert index[("Market", "Knowledge")] == (
        "openagrinet:MandiPrice",
        "openagrinet:MarketIntelligence",
    )
