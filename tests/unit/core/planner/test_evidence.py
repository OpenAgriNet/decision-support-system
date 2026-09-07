"""Tier 1 — assembling Evidence from what the loop's tools accumulated."""

from __future__ import annotations

import pytest

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.planner.evidence import assemble_evidence
from dss.core.planner.models import Failure, SourceKind
from dss.core.provider_discovery.models import DiscoveredAnswer

# One answer per capability this change touches. `attributes` holds a
# resource's `resourceAttributes` verbatim (see the adapter, which reads
# exactly that key off the wire), so these mirror the shapes network-specs
# publishes — WeatherObservation from fixtures/select_response.json, the rest
# from each pack's examples/.
#
# The four have nothing in common past the JSON-LD envelope: MandiPrice nests
# under `prices`, KnowledgeAdvisory carries `recommendations[]`,
# WeatherObservation `parameters[]`, AgricultureFacility `services[]`. That is
# the point of these cases — assembly reads none of it, so a capability the
# DSS has never seen still assembles.

MANDI_PRICE = DiscoveredAnswer(
    provider_id="agmarknet",
    provider_name="Agmarknet",
    capability="openagrinet:MandiPrice",
    resource_id="res:agmarknet:daily-price:2026-08-25",
    attributes={
        "commodity": {"code": "PADDY", "name": "Paddy"},
        "prices": {"modal": 2200, "unit": "INR/quintal"},
    },
    validity=None,
)

KNOWLEDGE_ADVISORY = DiscoveredAnswer(
    provider_id="krishi-kb",
    provider_name="Krishi Knowledge Base",
    capability="openagrinet:KnowledgeAdvisory",
    resource_id="res:krishi-kb:crop-advisory:cotton",
    attributes={
        "topics": ["Early-season cotton management"],
        "recommendations": [
            {
                "action": "Inspect seedlings",
                "message": "Inspect the field twice each week during establishment.",
                "language": "en",
                "priority": "Normal",
            }
        ],
        # Some packs carry their own provenance inside resourceAttributes,
        # sourceUri included. Assembly does not read it: Source.name comes
        # from the DiscoveredAnswer's provider_name, which every capability
        # has, and Source.url stays None. Mining a per-pack source block for
        # a citation URL is a follow-up, not something to guess at here.
        "source": {
            "sourceId": "participant:agriculture-knowledge-provider",
            "sourceName": "Agriculture Knowledge Provider",
            "sourceUri": "https://knowledge.example.org",
        },
    },
    validity=None,
)

WEATHER_OBSERVATION = DiscoveredAnswer(
    provider_id="aws-network",
    provider_name="Automatic Weather Station Network",
    capability="openagrinet:WeatherObservation",
    resource_id="res:aws-network:observation:ahilyanagar",
    attributes={
        "observationType": "Forecast",
        "source": {"sourceId": "mausamgram", "sourceName": "IMD Mausamgram NWP"},
        "parameters": [
            {
                "parameter": "Rainfall",
                "aggregation": "Total",
                "unit": "mm",
                "value": 0.84,
            }
        ],
    },
    validity=None,
)

AGRICULTURE_FACILITY = DiscoveredAnswer(
    provider_id="agri-common-services",
    provider_name="Agriculture Common Services Provider",
    capability="openagrinet:AgricultureFacility",
    resource_id="res:agri-common-services:facility:soil-testing",
    attributes={
        "facilityType": "SoilTestingFacility",
        "address": {
            "addressLocality": "Ahilyanagar",
            "addressRegion": "Maharashtra",
            "addressCountry": "IN",
        },
        "services": [{"code": "SOIL_TESTING", "name": "Soil sample testing"}],
        "source": {
            "sourceId": "provider:agriculture-common-services",
            "sourceName": "Agriculture Common Services Provider",
        },
    },
    validity=None,
)

PRICE_ASK = Ask(
    agriculture_subjects="paddy",
    subject_categories=SubjectCategory.MARKET,
    interaction_type=InteractionType.OBSERVE,
)


def _intent(*asks: Ask) -> Intent:
    return Intent(asks=asks, confidence=0.9)


@pytest.mark.parametrize(
    "answer",
    [MANDI_PRICE, KNOWLEDGE_ADVISORY, WEATHER_OBSERVATION, AGRICULTURE_FACILITY],
    ids=lambda answer: answer.capability.split(":")[-1],
)
def test_one_answer_becomes_one_source_and_one_result(
    answer: DiscoveredAnswer,
) -> None:
    """Every capability assembles the same way. ``Result.data`` is the pack's
    ``resourceAttributes`` verbatim — assembly does not read, reshape or
    validate them."""

    evidence = assemble_evidence([(0, answer)], intent=_intent(PRICE_ASK))

    assert len(evidence.sources) == 1
    source = evidence.sources[0]
    assert source.id == "1"
    assert source.name == answer.provider_name
    assert source.kind is SourceKind.PROVIDER
    assert source.url is None

    assert len(evidence.results) == 1
    result = evidence.results[0]
    assert result.ask_index == 0
    assert result.source_id == "1"
    assert result.data == answer.attributes


ADVISORY_ASK = Ask(
    agriculture_subjects="paddy",
    subject_categories=SubjectCategory.CROP,
    interaction_type=InteractionType.ADVISE,
)


def test_one_provider_answering_two_asks_is_one_source() -> None:
    """A provider is cited by name, so answering twice does not make it two
    sources — but it is still two results, one per ask."""

    second_ask_answer = DiscoveredAnswer(
        provider_id=MANDI_PRICE.provider_id,
        provider_name=MANDI_PRICE.provider_name,
        capability=MANDI_PRICE.capability,
        resource_id="res:agmarknet:daily-price:2026-08-26",
        attributes={"prices": {"modal": 2250}},
        validity=None,
    )

    evidence = assemble_evidence(
        [(0, MANDI_PRICE), (1, second_ask_answer)],
        intent=_intent(PRICE_ASK, ADVISORY_ASK),
    )

    assert [source.id for source in evidence.sources] == ["1"]
    assert [result.source_id for result in evidence.results] == ["1", "1"]
    assert [result.ask_index for result in evidence.results] == [0, 1]
    assert evidence.served == (0, 1)


def test_two_providers_are_numbered_in_first_seen_order() -> None:
    """The numbers are what a claim cites, so they follow the order results
    came back, not provider id or name."""

    evidence = assemble_evidence(
        [(0, MANDI_PRICE), (1, KNOWLEDGE_ADVISORY)],
        intent=_intent(PRICE_ASK, ADVISORY_ASK),
    )

    assert [(source.id, source.name) for source in evidence.sources] == [
        ("1", "Agmarknet"),
        ("2", "Krishi Knowledge Base"),
    ]
    assert [result.source_id for result in evidence.results] == ["1", "2"]


def test_no_answers_is_not_sufficient() -> None:
    """The loop ended without a single result, so there is nothing to answer
    from. ``sufficient`` is the composer's cue to say "I don't know" rather
    than invent."""

    evidence = assemble_evidence([], intent=_intent(PRICE_ASK))

    assert evidence.sources == ()
    assert evidence.results == ()
    assert evidence.served == ()
    assert evidence.sufficient is False


def test_any_answer_is_sufficient() -> None:
    evidence = assemble_evidence([(0, MANDI_PRICE)], intent=_intent(PRICE_ASK))

    assert evidence.sufficient is True


def test_failures_are_carried_onto_the_evidence() -> None:
    """The composer must be able to say "we could not reach Agmarknet" rather
    than "nobody serves this" — the same empty result, two very different
    things to tell a farmer."""

    failure = Failure(
        capability="openagrinet:MandiPrice",
        reason="too many requests",
        retryable=True,
    )

    evidence = assemble_evidence([], failures=[(0, failure)], intent=_intent(PRICE_ASK))

    assert evidence.failed == (failure,)
    assert evidence.sufficient is False
