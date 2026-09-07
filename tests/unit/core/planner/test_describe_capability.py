"""Tier 1 — rendering an ask's candidates and their filterable fields.

The model must see this before calling select: without it, nothing tells
the model which fields are valid for a given capability (e.g. "topics" for
KnowledgeAdvisory), so it would have to guess and rely on a retry.
"""

from __future__ import annotations

from dss.core.planner.describe_capability import render_candidates_as_markdown
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import ProviderCapability

_MANDI = ProviderCapability(
    provider_id="agmarknet",
    provider_name="Agmarknet",
    capability="openagrinet:MandiPrice",
    resource_id="res:agmarknet:daily-price",
    observed_categories=("Market",),
)
_KNOWLEDGE = ProviderCapability(
    provider_id="krishi-kb",
    provider_name="Krishi Knowledge Base",
    capability="openagrinet:KnowledgeAdvisory",
    resource_id="res:krishi-kb:crop-advisory",
    observed_categories=("Crop", "Knowledge"),
)
_SCHEMAS = {
    "openagrinet:MandiPrice": DomainSchema(
        type="MandiPrice", filterable=("commodity.code", "market.marketCode")
    ),
    "openagrinet:KnowledgeAdvisory": DomainSchema(
        type="KnowledgeAdvisory", filterable=("topics", "subjectCategories")
    ),
}


def test_renders_each_candidates_provider_and_resource_id() -> None:
    markdown = render_candidates_as_markdown((_MANDI,), schemas=_SCHEMAS)
    assert "Agmarknet" in markdown
    assert "res:agmarknet:daily-price" in markdown


def test_renders_each_candidates_filterable_fields() -> None:
    markdown = render_candidates_as_markdown((_KNOWLEDGE,), schemas=_SCHEMAS)
    assert "topics" in markdown
    assert "subjectCategories" in markdown


def test_renders_multiple_candidates() -> None:
    markdown = render_candidates_as_markdown((_MANDI, _KNOWLEDGE), schemas=_SCHEMAS)
    assert "Agmarknet" in markdown
    assert "Krishi Knowledge Base" in markdown


def test_no_candidates_says_so() -> None:
    markdown = render_candidates_as_markdown((), schemas=_SCHEMAS)
    assert "no" in markdown.lower()
