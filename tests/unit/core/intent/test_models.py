"""Tier 1 — the Intent and UserTurn contracts (spec 0002)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    Classification,
    ClassifiedAsk,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
    UnresolvedPlace,
)
from dss.core.shared.models import Geometry, UserTurn
from dss.ports.area_lookup import AreaMatch


def test_intent_defaults_are_empty() -> None:
    intent = Intent()
    assert intent.asks == ()
    assert intent.confidence == 0.0


def test_ask_carries_its_own_resolved_place() -> None:
    """Each ask resolves its own place — "wheat price in Pune and will it rain
    in Anand?" is two asks, two places."""

    place = ResolvedPlace(
        name="Pune",
        within=("India", "Maharashtra"),
        geometry=Geometry(coordinates=[73.85, 18.52]),
        source=PlaceSource.NAMED,
    )
    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )
    assert ask.place == place


def test_place_source_has_four_values() -> None:
    """Device geometry and a client-asserted area are different provenances
    — one is a raw coordinate, the other is a name resolved through the same
    lookup as a farmer-named place — so they need distinct source values."""

    assert {s.value for s in PlaceSource} == {
        "asserted_geometry",
        "named",
        "carried",
        "asserted_area",
    }


def test_ask_place_defaults_to_none() -> None:
    """No place needed and none named — a valid ask, not an error
    ("how do I grow potatoes" has nothing to resolve)."""

    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
    )
    assert ask.place is None


def test_ask_carries_an_ambiguous_place() -> None:
    """A name matching several places is not a resolved place, but it is not
    nothing either — the candidates ride along so the farmer can be asked
    which one."""

    candidates = (
        AreaMatch(
            name="Bilaspur",
            region="IN-HP",
            within=("India", "Himachal Pradesh"),
            geometry=Geometry(coordinates=[76.75, 31.33]),
        ),
        AreaMatch(
            name="Bilaspur",
            region="IN-CT",
            within=("India", "Chhattisgarh"),
            geometry=Geometry(coordinates=[82.15, 22.09]),
        ),
    )
    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=AmbiguousPlace(unresolved_name="Bilaspur", candidates=candidates),
    )
    assert isinstance(ask.place, AmbiguousPlace)
    assert ask.place.unresolved_name == "Bilaspur"
    assert ask.place.candidates == candidates


def test_ask_carries_an_unresolved_place() -> None:
    """A name the index does not carry — different from `None`, which means
    nothing was named at all."""

    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=UnresolvedPlace(unresolved_name="Xyzzy"),
    )
    assert isinstance(ask.place, UnresolvedPlace)
    assert ask.place.unresolved_name == "Xyzzy"


def test_resolved_place_carries_its_ancestor_chain() -> None:
    """Coarsest first, no level words — a chain works for any adopter's
    taxonomy."""

    place = ResolvedPlace(
        name="Baramati",
        within=("India", "Maharashtra", "Pune"),
        geometry=Geometry(coordinates=[74.58, 18.15]),
        source=PlaceSource.NAMED,
    )
    assert place.name == "Baramati"
    assert place.within == ("India", "Maharashtra", "Pune")
    assert place.geometry.type == "Point"
    assert place.geometry.coordinates == [74.58, 18.15]
    assert place.source == PlaceSource.NAMED


def test_resolved_place_is_frozen() -> None:
    place = ResolvedPlace(
        name="Pune",
        within=("India", "Maharashtra"),
        geometry=Geometry(coordinates=[73.85, 18.52]),
        source=PlaceSource.ASSERTED_GEOMETRY,
    )
    with pytest.raises(ValidationError):
        place.name = "Anand"


def test_resolved_place_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        ResolvedPlace(
            name="Pune",
            within=(),
            geometry=Geometry(coordinates=[73.85, 18.52]),
            source=PlaceSource.ASSERTED_GEOMETRY,
            level="district",
        )


def test_classified_ask_carries_a_place_name_not_a_place() -> None:
    """This is what the LLM returns — words only. A nested ``ResolvedPlace``
    here would invite an invented geometry; only the resolver may fill one."""

    ask = ClassifiedAsk(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place_name="Pune",
    )
    assert ask.place_name == "Pune"


def test_classification_rejects_a_domain_ask() -> None:
    """Proves the split is a real type boundary, not a rename: the LLM schema
    cannot be handed a domain ``Ask`` carrying a ``ResolvedPlace``."""

    ask = Ask(
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    with pytest.raises(ValidationError):
        Classification(asks=(ask,))


def test_intent_carries_asks_and_confidence() -> None:
    ask = Ask(
        agriculture_subjects="potato",
        subject_categories=SubjectCategory.MARKET,
        interaction_type=InteractionType.OBSERVE,
    )
    intent = Intent(asks=(ask,), confidence=0.9)
    assert intent.asks == (ask,)
    assert intent.confidence == 0.9


def test_ask_subject_may_be_none() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
    )
    assert ask.agriculture_subjects is None


def test_interaction_type_values() -> None:
    assert {i.value for i in InteractionType} == {"advise", "observe", "act"}


def test_subject_category_values() -> None:
    """`Facility` is here because `AgricultureFacility` requires it; `Practice`
    is the network's seventh category and is deliberately absent until a pack
    serves it."""

    assert {c.value for c in SubjectCategory} == {
        "Crop",
        "Livestock",
        "Weather",
        "Market",
        "Scheme",
        "Facility",
    }


def test_confidence_out_of_range_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(confidence=1.5)


def test_unknown_subject_category_raises() -> None:
    with pytest.raises(ValidationError):
        Ask(subject_categories="Fishery", interaction_type=InteractionType.ADVISE)


def test_unknown_field_raises() -> None:
    with pytest.raises(ValidationError):
        Intent(primary_domian="dairy")


def test_intent_is_frozen() -> None:
    intent = Intent()
    with pytest.raises(ValidationError):
        intent.confidence = 0.9


def test_user_turn_requires_both_query_fields() -> None:
    with pytest.raises(ValidationError):
        UserTurn(
            original_query="hi",
            session_id="s",
            source_lang="en",
            target_lang="en",
            channel="web",
        )


def test_non_bcp47_language_raises() -> None:
    with pytest.raises(ValidationError):
        UserTurn(
            original_query="hi",
            enriched_query="hi",
            session_id="s",
            transaction_id="t",
            source_lang="gujarati",  # full name, not a code
            target_lang="en",
            channel="web",
        )
