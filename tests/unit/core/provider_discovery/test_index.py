"""Tests for building the capability index from schema pack files."""

from __future__ import annotations

import json

from dss.core.provider_discovery.index import (
    build_capability_index,
    build_schema_context_index,
)
from dss.core.provider_discovery.models import SchemaPackFiles

# Trimmed from the real MandiPrice/v0.1/attributes.yaml, keeping both places
# `@type` appears.
#
# `x-jsonld` is what the index reads: a plain scalar, and `@context` sits
# beside it so both come from one place.
#
# `properties["@type"]` also declares the type, but as a `oneOf` — the schema
# lets a provider send either the canonical string or an array that contains
# it. The earlier fixture simplified that to `{const: ...}`, so the extractor
# was written to read `["const"]` directly and raised `KeyError('const')` on
# every real pack, leaving the capability index empty.
MANDI_PRICE_ATTRIBUTES = """
components:
  schemas:
    MandiPrice:
      type: object
      x-beckn-container: resourceAttributes
      x-jsonld:
        "@context": "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
        "@type": openagrinet:MandiPrice
      allOf:
        - type: object
          properties:
            "@type":
              oneOf:
                - type: string
                  const: openagrinet:MandiPrice
                - type: array
                  contains:
                    const: openagrinet:MandiPrice
"""

MANDI_PRICE_EXAMPLE = '{"subjectCategories": ["Market"]}'

MARKET_INTELLIGENCE_ATTRIBUTES = """
components:
  schemas:
    MarketIntelligence:
      type: object
      x-jsonld:
        "@context": "https://schemas.openagrinet.global/schema/MarketIntelligence/v0.1/context.jsonld"
        "@type": openagrinet:MarketIntelligence
"""

MARKET_INTELLIGENCE_EXAMPLE = '{"subjectCategories": ["Market"]}'


def test_a_single_pack_maps_its_category_to_its_type() -> None:
    pack = SchemaPackFiles(
        pack_name="MandiPrice",
        version="v0.1",
        profile_json="{}",
        attributes_yaml=MANDI_PRICE_ATTRIBUTES,
        examples_json=(MANDI_PRICE_EXAMPLE,),
    )

    index, _ = build_capability_index((pack,))

    assert index[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)
    assert index[("Market", "Service")] == ("openagrinet:MandiPrice",)


def test_two_distinct_packs_sharing_a_category_both_appear() -> None:
    mandi_price = SchemaPackFiles(
        pack_name="MandiPrice",
        version="v0.1",
        profile_json="{}",
        attributes_yaml=MANDI_PRICE_ATTRIBUTES,
        examples_json=(MANDI_PRICE_EXAMPLE,),
    )
    market_intelligence = SchemaPackFiles(
        pack_name="MarketIntelligence",
        version="v0.1",
        profile_json="{}",
        attributes_yaml=MARKET_INTELLIGENCE_ATTRIBUTES,
        examples_json=(MARKET_INTELLIGENCE_EXAMPLE,),
    )

    index, _ = build_capability_index((mandi_price, market_intelligence))

    assert index[("Market", "Knowledge")] == (
        "openagrinet:MandiPrice",
        "openagrinet:MarketIntelligence",
    )


def test_a_pack_spanning_many_categories_indexes_them_in_a_stable_order() -> None:
    """Categories are collected into a set, whose iteration order over strings
    varies with PYTHONHASHSEED. The resulting @type order reaches the discover
    request's jsonpath filter, so two identical deployments must not build
    different requests from the same packs.
    """
    categories = ["Market", "Weather", "Scheme", "Advisory", "Livestock", "Crop"]
    pack = SchemaPackFiles(
        pack_name="MandiPrice",
        version="v0.1",
        profile_json="{}",
        attributes_yaml=MANDI_PRICE_ATTRIBUTES,
        examples_json=tuple(
            json.dumps({"subjectCategories": [category]}) for category in categories
        ),
    )

    index, _ = build_capability_index((pack,))

    assert list(index) == [
        (category, action_type)
        for category in sorted(categories)
        for action_type in ("Knowledge", "Service")
    ]


MISNAMED_SCHEMA_ATTRIBUTES = """
components:
  schemas:
    SomethingElse:
      allOf:
        - type: object
          properties:
            "@type":
              const: openagrinet:Whatever
"""


def _pack(name: str, attributes: str, example: str) -> SchemaPackFiles:
    return SchemaPackFiles(
        pack_name=name,
        version="v0.1",
        profile_json="{}",
        attributes_yaml=attributes,
        examples_json=(example,),
    )


def test_a_pack_whose_schema_key_differs_from_its_name_is_skipped() -> None:
    """network-specs is an external checkout — a third party can break one
    pack. That must not blind every other capability.
    """
    good = _pack("MandiPrice", MANDI_PRICE_ATTRIBUTES, MANDI_PRICE_EXAMPLE)
    misnamed = _pack("MisnamedPack", MISNAMED_SCHEMA_ATTRIBUTES, MANDI_PRICE_EXAMPLE)

    index, skipped = build_capability_index((good, misnamed))

    assert index[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)
    assert len(skipped) == 1
    assert skipped[0].pack_name == "MisnamedPack"
    assert "MisnamedPack" in skipped[0].reason


def test_a_pack_whose_example_has_no_subject_categories_is_skipped() -> None:
    good = _pack("MandiPrice", MANDI_PRICE_ATTRIBUTES, MANDI_PRICE_EXAMPLE)
    no_categories = _pack(
        "MarketIntelligence", MARKET_INTELLIGENCE_ATTRIBUTES, '{"notCategories": []}'
    )

    index, skipped = build_capability_index((good, no_categories))

    assert index[("Market", "Knowledge")] == ("openagrinet:MandiPrice",)
    assert [s.pack_name for s in skipped] == ["MarketIntelligence"]


def test_every_pack_being_good_skips_nothing() -> None:
    good = _pack("MandiPrice", MANDI_PRICE_ATTRIBUTES, MANDI_PRICE_EXAMPLE)

    _index, skipped = build_capability_index((good,))

    assert skipped == ()


def test_a_bad_pack_is_skipped_from_the_schema_context_index_too() -> None:
    """Both indexes must skip the same packs, or the capability index can
    resolve a @type the schema-context index can't map to a pack.
    """
    good = _pack("MandiPrice", MANDI_PRICE_ATTRIBUTES, MANDI_PRICE_EXAMPLE)
    misnamed = _pack("MisnamedPack", MISNAMED_SCHEMA_ATTRIBUTES, MANDI_PRICE_EXAMPLE)

    index, skipped = build_schema_context_index((good, misnamed))

    assert index == {
        "openagrinet:MandiPrice": "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
    }
    assert [s.pack_name for s in skipped] == ["MisnamedPack"]


def test_schema_context_index_maps_a_type_to_the_url_the_pack_declares() -> None:
    """The pack declares its own ``@context`` under ``x-jsonld``, so the index
    carries that URL rather than the parts to rebuild it."""

    pack = SchemaPackFiles(
        pack_name="MandiPrice",
        version="v0.1",
        profile_json="{}",
        attributes_yaml=MANDI_PRICE_ATTRIBUTES,
        examples_json=(MANDI_PRICE_EXAMPLE,),
    )

    index, _ = build_schema_context_index((pack,))

    assert index["openagrinet:MandiPrice"] == (
        "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
    )
