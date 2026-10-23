"""Tier 1 — what a client's ranking does to what discovery found.

Plain data in, plain data out: no network, no agent, no provider call. The
ordering rule is the whole business decision in this story, so it is tested
here rather than through the planner.
"""

from __future__ import annotations

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.provider_preferences.models import NO_PREFERENCE, PreferenceBook
from dss.core.provider_preferences.service import prioritise, priority_for_ask

WEATHER = "openagrinet:WeatherObservation"


def _capability(provider: str, resource: str = "res:1") -> ProviderCapability:
    return ProviderCapability(
        provider_id=provider,
        provider_name=provider.title(),
        capability=WEATHER,
        resource_id=f"{resource}:{provider}",
    )


def _answer(provider: str) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id=provider,
        provider_name=provider.title(),
        capability=WEATHER,
        resource_id=f"res:direct:{provider}",
        attributes={},
        validity=None,
    )


def _intent(*categories: SubjectCategory) -> Intent:
    return Intent(
        asks=tuple(
            Ask(subject_categories=category, interaction_type=InteractionType.OBSERVE)
            for category in (categories or (SubjectCategory.WEATHER,))
        ),
        confidence=1.0,
    )


def _discovered(
    capabilities: tuple[ProviderCapability, ...] = (),
    answers: tuple[DiscoveredAnswer, ...] = (),
) -> DiscoveryResult:
    return DiscoveryResult(
        answers={0: answers} if answers else {},
        capabilities={0: capabilities} if capabilities else {},
        failures={},
        events=(),
    )


def _book(**senders: dict[str, tuple[str, ...]]) -> PreferenceBook:
    return PreferenceBook(default={}, senders=senders)


def test_the_first_choice_provider_is_consulted_first() -> None:
    discovery = _discovered((_capability("imd"), _capability("vistaar")))
    preference = _book(app={"weather": ("vistaar", "imd")}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert [c.provider_id for c in result.capabilities[0]] == ["vistaar", "imd"]


def test_a_provider_the_ranking_does_not_name_is_dropped() -> None:
    """A ranking is exclusive. This is the risk the story accepted: a capable
    provider nobody vetted answers nothing."""

    discovery = _discovered((_capability("imd"), _capability("unvetted")))
    preference = _book(app={"weather": ("imd",)}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert [c.provider_id for c in result.capabilities[0]] == ["imd"]
    assert result.priorities[0].dropped == ("unvetted",)


def test_answers_the_catalog_already_held_are_ranked_too() -> None:
    """A Direct answer needs no provider call, so nothing downstream would
    have filtered it — and being served by a provider the client ruled out is
    the same wrong answer whether or not we had to ask for it."""

    discovery = _discovered(answers=(_answer("unvetted"), _answer("imd")))
    preference = _book(app={"weather": ("imd",)}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert [a.provider_id for a in result.answers[0]] == ["imd"]


def test_direct_answers_come_from_the_best_ranked_provider_only() -> None:
    """One ask is answered by one provider. Direct answers skip the planner's
    first-that-answered funnel, so the cut happens here — without it an ask
    the catalog answered twice over would blend two providers' data."""

    discovery = _discovered(answers=(_answer("imd"), _answer("vistaar")))
    preference = _book(app={"weather": ("vistaar", "imd")}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert [a.provider_id for a in result.answers[0]] == ["vistaar"]
    # The runner-up stays on the record, so the order can be judged later.
    assert result.priorities[0].kept == ("vistaar", "imd")


def test_a_topic_nobody_ranked_keeps_every_provider_in_discovery_order() -> None:
    """The behaviour that existed before any preference did."""

    discovery = _discovered((_capability("imd"), _capability("vistaar")))
    preference = _book(app={"market": ("agmarknet",)}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert [c.provider_id for c in result.capabilities[0]] == ["imd", "vistaar"]
    assert result.priorities[0].applied is False


def test_no_preference_at_all_leaves_discovery_untouched() -> None:
    discovery = _discovered((_capability("imd"), _capability("vistaar")))

    result = prioritise(discovery, intent=_intent(), preference=NO_PREFERENCE)

    assert result.capabilities == discovery.capabilities
    assert priority_for_ask(result, ask_index=0) is None


def test_two_clients_asking_the_same_question_get_their_own_first_choice() -> None:
    discovery = _discovered((_capability("imd"), _capability("vistaar")))
    book = _book(
        one={"weather": ("imd", "vistaar")}, two={"weather": ("vistaar", "imd")}
    )

    first = prioritise(discovery, intent=_intent(), preference=book.for_sender("one"))
    second = prioritise(discovery, intent=_intent(), preference=book.for_sender("two"))

    assert first.capabilities[0][0].provider_id == "imd"
    assert second.capabilities[0][0].provider_id == "vistaar"


def test_each_ask_is_ranked_on_its_own_topic() -> None:
    discovery = DiscoveryResult(
        answers={},
        capabilities={
            0: (_capability("imd"), _capability("vistaar")),
            1: (_capability("agmarknet"), _capability("state-mandi")),
        },
        failures={},
        events=(),
    )
    preference = _book(
        app={"weather": ("vistaar",), "market": ("state-mandi",)}
    ).for_sender("app")

    result = prioritise(
        discovery,
        intent=_intent(SubjectCategory.WEATHER, SubjectCategory.MARKET),
        preference=preference,
    )

    assert [c.provider_id for c in result.capabilities[0]] == ["vistaar"]
    assert [c.provider_id for c in result.capabilities[1]] == ["state-mandi"]


def test_one_providers_several_resources_keep_discoverys_order() -> None:
    """The ranking orders providers. It says nothing about which of one
    provider's own resources answers better, so that order is left alone."""

    discovery = _discovered(
        (
            _capability("imd", "res:b"),
            _capability("imd", "res:a"),
            _capability("vistaar"),
        )
    )
    preference = _book(app={"weather": ("imd", "vistaar")}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert [c.resource_id for c in result.capabilities[0]] == [
        "res:b:imd",
        "res:a:imd",
        "res:1:vistaar",
    ]


def test_a_ranking_that_matches_nobody_leaves_the_ask_with_no_provider() -> None:
    """Not an error. The farmer is told nothing could be found, which is what
    an exclusive ranking means when none of its providers serve the area."""

    discovery = _discovered((_capability("imd"),))
    preference = _book(app={"weather": ("maharashtra-vistaar",)}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert result.capabilities[0] == ()
    assert result.priorities[0].applied is True
    assert result.priorities[0].kept == ()


def test_the_record_says_which_layer_answered_and_what_it_dropped() -> None:
    """What an operator reads when one client's answers come back thinner
    than another's for the same question."""

    discovery = _discovered((_capability("imd"), _capability("unvetted")))
    book = PreferenceBook(default={"weather": ("imd",)}, senders={"app": {}})

    result = prioritise(discovery, intent=_intent(), preference=book.for_sender("app"))

    priority = result.priorities[0]
    assert priority.sender_id == "app"
    assert priority.capability == "Weather"
    assert priority.source == "default"
    assert priority.ranking == ("imd",)
    assert priority.kept == ("imd",)
    assert priority.dropped == ("unvetted",)


def test_an_ask_discovery_found_nobody_for_gains_no_entry() -> None:
    """Filtering must not turn "absent from the result" into "found nobody" —
    the same thing to the farmer, one more key for every later reader."""

    discovery = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    preference = _book(app={"weather": ("imd",)}).for_sender("app")

    result = prioritise(discovery, intent=_intent(), preference=preference)

    assert result.capabilities == {}
    assert result.answers == {}
    assert result.priorities[0].applied is True
