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


def test_renders_what_a_candidate_advertises() -> None:
    """The model has to write a commodity code it cannot invent. The provider
    advertises its own vocabulary in the catalog, so show it — otherwise the
    model guesses, and nothing between here and the provider checks the value.

    Both the code and the name: the code alone is unusable, and the name alone
    is what the model already had from the farmer.
    """

    mandi = ProviderCapability(
        provider_id="agmarknet",
        provider_name="Agmarknet",
        capability="openagrinet:MandiPrice",
        resource_id="res:agmarknet:daily-price",
        observed_categories=("Market",),
        advertised={
            "supportedCommodities": [
                {"code": "1", "name": "Wheat"},
                {"code": "78", "name": "Tomato"},
            ]
        },
    )

    markdown = render_candidates_as_markdown((mandi,), schemas=_SCHEMAS)

    assert "supportedCommodities" in markdown
    assert "78" in markdown
    assert "Tomato" in markdown


def test_a_scalar_advertised_field_is_left_out() -> None:
    """A resource advertises two different kinds of thing side by side: a
    vocabulary the model may choose from (``supportedCommodities``), and a
    fact about the provider (``historyPeriod: P1Y``,
    ``historicalDataAvailable: true``).

    Only the first is a set of values a ``select`` call may carry. Listing the
    second under a heading that promises values the provider serves invited
    the model to send ``historicalDataAvailable`` as a filter — and the
    real MandiPrice catalog advertises three such scalars to two vocabularies.

    A list is the signal, not a field-name prefix: ``supported*`` is
    MandiPrice's own naming, and no other pack is bound by it.
    """

    mandi = ProviderCapability(
        provider_id="agmarknet",
        provider_name="Agmarknet",
        capability="openagrinet:MandiPrice",
        resource_id="res:agmarknet:daily-price",
        observed_categories=("Market",),
        advertised={
            "supportedCommodities": [{"code": "78", "name": "Tomato"}],
            "historyPeriod": "P1Y",
            "historicalDataAvailable": True,
        },
    )

    markdown = render_candidates_as_markdown((mandi,), schemas=_SCHEMAS)

    assert "supportedCommodities" in markdown
    assert "historyPeriod" not in markdown
    assert "historicalDataAvailable" not in markdown


def test_renders_multiple_candidates() -> None:
    markdown = render_candidates_as_markdown((_MANDI, _KNOWLEDGE), schemas=_SCHEMAS)
    assert "Agmarknet" in markdown
    assert "Krishi Knowledge Base" in markdown


def test_no_candidates_says_so() -> None:
    markdown = render_candidates_as_markdown((), schemas=_SCHEMAS)
    assert "no" in markdown.lower()


def test_a_candidate_with_no_indexed_schema_is_left_out() -> None:
    """Discovery reports what the network offers; the schema index is built
    from the packs on disk. They can disagree — a skipped pack leaves the
    index without a @type the network still advertises.

    Raising ``KeyError`` on that candidate lost the *other* candidates for
    the same ask, which were usable. This tool touches nothing external and
    must not be able to end a turn."""

    unindexed = ProviderCapability(
        provider_id="gj-agri",
        provider_name="Gujarat Agriculture Dept",
        capability="openagrinet:AgricultureFacility",
        resource_id="res:gj-agri:facility",
        observed_categories=("Service",),
    )

    markdown = render_candidates_as_markdown((_MANDI, unindexed), schemas=_SCHEMAS)

    assert "Agmarknet" in markdown
    assert "Gujarat Agriculture Dept" not in markdown


def test_every_candidate_being_unindexed_says_so() -> None:
    """Otherwise the model gets an empty string and no idea why."""

    unindexed = ProviderCapability(
        provider_id="gj-agri",
        provider_name="Gujarat Agriculture Dept",
        capability="openagrinet:AgricultureFacility",
        resource_id="res:gj-agri:facility",
        observed_categories=("Service",),
    )

    markdown = render_candidates_as_markdown((unindexed,), schemas=_SCHEMAS)

    assert "no" in markdown.lower()


def test_a_field_advertised_under_a_filterable_path_is_left_out() -> None:
    """A provider's ``topics`` is it describing what it holds, not a vocabulary.

    The pack declares ``topics`` as a bare array of strings with no ``enum``,
    indexes it, and expects the caller to compose the filter from what the
    farmer said. Rendering it under "this provider serves only these values"
    told the model otherwise, and the model answered "can i grow potato" by
    sending back the provider's own ``Crop establishment`` instead of the
    farmer's subject.

    The name is the signal: a vocabulary governs a filter and is named apart
    from it (``supportedCommodities`` for ``commodity.code``), so a resource
    advertising under the very path you would filter on is publishing content,
    not an enum.
    """

    knowledge = ProviderCapability(
        provider_id="krishi-kb",
        provider_name="Krishi Knowledge Base",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:krishi-kb:crop-advisory",
        observed_categories=("Crop",),
        advertised={"topics": ["Crop establishment", "Package of practices"]},
    )

    markdown = render_candidates_as_markdown((knowledge,), schemas=_SCHEMAS)

    assert "Crop establishment" not in markdown
    assert "Package of practices" not in markdown
    assert "serves only these values" not in markdown
    # still settable — it is one of the pack's filterable paths
    assert "topics" in markdown


def test_a_vocabulary_of_plain_strings_is_still_rendered() -> None:
    """Shape is not the signal. WeatherObservation advertises
    ``supportedParameters: ["Rainfall", "Temperature"]`` — bare strings, but a
    genuine vocabulary the model cannot invent, governing the filterable
    ``parameters``.

    Dropping every plain-string list would have taken it away. This is the
    assertion that says so.
    """

    weather = ProviderCapability(
        provider_id="imd",
        provider_name="IMD",
        capability="openagrinet:WeatherObservation",
        resource_id="res:imd:forecast",
        observed_categories=("Weather",),
        advertised={"supportedParameters": ["Rainfall", "Temperature"]},
    )
    schemas = {
        "openagrinet:WeatherObservation": DomainSchema(
            type="WeatherObservation", filterable=("observationType", "parameters")
        )
    }

    markdown = render_candidates_as_markdown((weather,), schemas=schemas)

    assert "supportedParameters: Rainfall, Temperature" in markdown


def test_a_vocabulary_under_a_compound_filterable_path_is_still_rendered() -> None:
    """``agricultureSubjects`` sits under the filterable
    ``agricultureSubjects[].subjectId``, so a prefix match would drop it — but
    it is a real crop vocabulary the model resolves the farmer's word against.

    Matching is exact for this reason. How the entry renders is a separate
    matter: ``_render_item`` only unpacks a top-level ``code``, so a nested
    descriptor falls through to ``str``. That wart is not this test's subject;
    being present is.
    """

    knowledge = ProviderCapability(
        provider_id="krishi-kb",
        provider_name="Krishi Knowledge Base",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:krishi-kb:crop-advisory",
        observed_categories=("Crop",),
        advertised={
            "agricultureSubjects": [
                {
                    "subjectId": "https://taxonomy.openagrinet.global/crops/cotton",
                    "descriptor": {"code": "COTTON", "name": "Cotton"},
                }
            ]
        },
    )
    schemas = {
        "openagrinet:KnowledgeAdvisory": DomainSchema(
            type="KnowledgeAdvisory",
            filterable=("topics", "agricultureSubjects[].subjectId"),
        )
    }

    markdown = render_candidates_as_markdown((knowledge,), schemas=schemas)

    assert "agricultureSubjects" in markdown
    assert "COTTON" in markdown


def test_a_vocabulary_is_kept_while_self_described_content_is_dropped() -> None:
    """Both on one candidate — the real KnowledgeAdvisory catalog carries
    exactly this pair."""

    knowledge = ProviderCapability(
        provider_id="krishi-kb",
        provider_name="Krishi Knowledge Base",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:krishi-kb:crop-advisory",
        observed_categories=("Crop",),
        advertised={
            "supportedCrops": [{"code": "COTTON", "name": "Cotton"}],
            "topics": ["Crop establishment"],
        },
    )

    markdown = render_candidates_as_markdown((knowledge,), schemas=_SCHEMAS)

    assert "COTTON=Cotton" in markdown
    assert "Crop establishment" not in markdown
