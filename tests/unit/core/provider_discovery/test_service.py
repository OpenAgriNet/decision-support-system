"""Tests for the @type resolver."""

from __future__ import annotations

from dss.core.intent.models import (
    Ask,
    InteractionType,
    SubjectCategory,
    UnclearAction,
)
from dss.core.provider_discovery.models import CapabilityUnresolved
from dss.core.provider_discovery.service import (
    capability_types_for,
    resolve_capability_type,
)
from dss.core.shared.models import ProviderAction


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


def test_an_asks_types_follow_its_interaction() -> None:
    """Advice reads the Knowledge packs; a lookup or an action, the Service
    ones — the same pairing discovery searches by."""

    index = {
        ("Market", "Knowledge"): ("MarketIntelligence",),
        ("Market", "Service"): ("MandiPrice",),
    }

    def ask(interaction: InteractionType) -> Ask:
        return Ask(
            subject_categories=SubjectCategory.MARKET, interaction_type=interaction
        )

    assert capability_types_for(ask(InteractionType.ADVISE), index) == (
        "MarketIntelligence",
    )
    assert capability_types_for(ask(InteractionType.OBSERVE), index) == ("MandiPrice",)


def test_an_ask_with_an_action_is_a_service_ask() -> None:
    """ "How do I file a complaint" is worded as advice, and still wants the
    desk that files one."""

    index = {("Grievance", "Service"): ("openagrinet:Grievance",)}
    how_to_file = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ADVISE,
        action=ProviderAction.SUPPORT,
    )

    assert capability_types_for(how_to_file, index) == ("openagrinet:Grievance",)


def test_an_unclear_action_leaves_the_wording_to_decide() -> None:
    """ "There is a problem with my wheat" names nothing to do yet, so it is
    still advice, and the advice packs can answer it."""

    index = {
        ("Crop", "Knowledge"): ("CropAdvisory",),
        ("Crop", "Service"): ("CropService",),
    }
    problem = Ask(
        subject_categories=SubjectCategory.CROP,
        interaction_type=InteractionType.ADVISE,
        action=UnclearAction.UNCLEAR,
    )

    assert capability_types_for(problem, index) == ("CropAdvisory",)
