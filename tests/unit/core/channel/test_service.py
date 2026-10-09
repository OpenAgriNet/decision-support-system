"""Tier 1 — the deterministic channel shaping.

The prose-writing composer lives in `core/channel/compose.py` (it needs an
LLM); what stays here is the fixed no-match reply. `answer_from_evidence` has
its own test alongside this one.
"""

from __future__ import annotations

from dss.core.channel.models import ClarificationText
from dss.core.channel.service import (
    NO_MATCH_TEXT,
    answer_for_unplaced_asks,
    answer_for_unserved_asks,
    no_match_answer,
    question_for_ambiguous_asks,
)
from dss.core.intent.models import (
    AmbiguousPlace,
    Ask,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
    UnresolvedPlace,
)
from dss.core.shared.models import Geometry
from dss.ports.area_lookup import AreaMatch

_TEXT = ClarificationText(
    needs_place="Which place are you asking about?",
    unknown_place="I could not find {name}.",
    unknown_place_in="I could not find {name} in {part}.",
    region_place="{name} is a big area. Which district or village in {name}?",
    ambiguous_place_header="Which {name}?",
    grouped_place_header="{name} is in several places. Which one:",
    more_places_hint="Not in this list? Tell me the area it is in.",
    no_provider_for="I could not find a source that answers this for {place} yet.",
    no_provider="I could not find a source that answers this yet.",
)


def test_no_match_answer_has_one_block_and_no_sources() -> None:
    answer = no_match_answer()

    assert len(answer.content) == 1
    assert answer.content[0].text == NO_MATCH_TEXT
    # nothing was consulted, so nothing is cited
    assert answer.sources == ()
    assert answer.content[0].source_ids == ()


def test_an_ambiguous_place_numbers_each_candidate() -> None:
    """A stable numbered list lets a later turn read the candidates back from
    history and pick by number, so nothing has to be invented."""

    bilaspur = AmbiguousPlace(
        unresolved_name="Bilaspur",
        candidates=(
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
        ),
    )

    answer = answer_for_unplaced_asks((_ask(place=bilaspur),), _TEXT)

    assert answer is not None
    text = answer.content[0].text
    assert "Bilaspur" in text
    assert "1. Bilaspur, Himachal Pradesh" in text
    assert "2. Bilaspur, Chhattisgarh" in text
    assert answer.sources == ()


def test_each_choice_shows_the_part_of_its_chain_that_differs() -> None:
    """Both Ashtis are in Maharashtra, so the state tells them apart for no
    one. The district does."""

    ashti = AmbiguousPlace(
        unresolved_name="Ashti",
        candidates=(
            AreaMatch(
                name="Ashti",
                region="IN-MH",
                within=("India", "Maharashtra", "Wardha"),
                geometry=Geometry(coordinates=[78.18, 21.2]),
            ),
            AreaMatch(
                name="Ashti",
                region="IN-MH",
                within=("India", "Maharashtra", "Beed"),
                geometry=Geometry(coordinates=[75.2, 18.8]),
            ),
        ),
    )

    answer = answer_for_unplaced_asks((_ask(place=ashti),), _TEXT)

    assert answer is not None
    text = answer.content[0].text
    assert "1. Ashti, Wardha" in text
    assert "2. Ashti, Beed" in text


def test_the_same_failed_place_is_reported_once() -> None:
    """ "Xyzzy price and Xyzzy weather" is one place the farmer has to fix,
    not two."""

    xyzzy = UnresolvedPlace(unresolved_name="Xyzzy")

    answer = answer_for_unplaced_asks((_ask(place=xyzzy), _ask(place=xyzzy)), _TEXT)

    assert answer is not None
    assert answer.content[0].text.count("I could not find Xyzzy.") == 1


def test_not_found_says_in_not_comma() -> None:
    """A farmer may not read "Aurangabad, Maharashtra" as "in". Say it."""

    place = UnresolvedPlace(unresolved_name="Aurangabad, Maharashtra")

    answer = answer_for_unplaced_asks((_ask(place=place),), _TEXT)

    assert answer is not None
    assert answer.content[0].text == "I could not find Aurangabad in Maharashtra."


def test_a_single_choice_shows_the_place_it_sits_in() -> None:
    """With one choice there is nothing to tell it apart from. "Kanha Chatti,
    India" helps nobody; the district does."""

    guess = AreaMatch(
        name="Kanha Chatti",
        region="IN-JH",
        within=("India", "Jharkhand", "Chatra"),
        geometry=Geometry(coordinates=[84.9, 24.2]),
        is_guess=True,
    )
    place = AmbiguousPlace(unresolved_name="Kanha", candidates=(guess,))

    answer = answer_for_unplaced_asks((_ask(place=place),), _TEXT)

    assert answer is not None
    assert answer.content[0].text == "Which Kanha?\n1. Kanha Chatti, Chatra"


def test_a_whole_state_asks_for_a_smaller_place() -> None:
    """Maharashtra exists. "I could not find Maharashtra" would be untrue; it is
    just too big to answer for one spot in it."""

    place = UnresolvedPlace(unresolved_name="Maharashtra", region="Maharashtra")

    answer = answer_for_unplaced_asks((_ask(place=place),), _TEXT)

    assert answer is not None
    assert answer.content[0].text == (
        "Maharashtra is a big area. Which district or village in Maharashtra?"
    )


def test_each_choice_stops_where_it_stands_apart_from_every_other() -> None:
    """Three Akbarpurs, two in Uttar Pradesh. The state is enough for the
    Bihar one; the two in Uttar Pradesh only differ by district."""

    def akbarpur(*within: str) -> AreaMatch:
        return AreaMatch(
            name="Akbarpur",
            region="IN-XX",
            within=("India", *within),
            geometry=Geometry(coordinates=[80.0, 26.0]),
        )

    place = AmbiguousPlace(
        unresolved_name="Akbarpur",
        candidates=(
            akbarpur("Bihar", "Nawada"),
            akbarpur("Uttar Pradesh", "Kanpur Dehat"),
            akbarpur("Uttar Pradesh", "Ambedkar Nagar"),
        ),
    )

    answer = answer_for_unplaced_asks((_ask(place=place),), _TEXT)

    assert answer is not None
    text = answer.content[0].text
    assert "1. Akbarpur, Bihar" in text
    assert "2. Akbarpur, Kanpur Dehat" in text
    assert "3. Akbarpur, Ambedkar Nagar" in text


def test_a_long_list_is_grouped_one_level_up() -> None:
    """Six Rampurs, two in each of three states. Six lines are too many to
    read; the farmer picks a state first, and the next turn narrows further."""

    def rampur(*within: str) -> AreaMatch:
        return AreaMatch(
            name="Rampur",
            region="IN-XX",
            within=("India", *within),
            geometry=Geometry(coordinates=[80.0, 26.0]),
        )

    place = AmbiguousPlace(
        unresolved_name="Rampur",
        candidates=(
            rampur("Uttar Pradesh", "Moradabad"),
            rampur("Uttar Pradesh", "Rampur"),
            rampur("Himachal Pradesh", "Shimla"),
            rampur("Himachal Pradesh", "Kullu"),
            rampur("Odisha", "Cuttack"),
            rampur("Odisha", "Puri"),
        ),
    )

    answer = answer_for_unplaced_asks((_ask(place=place),), _TEXT)

    assert answer is not None
    assert answer.content[0].text.splitlines() == [
        "Rampur is in several places. Which one:",
        "1. Rampur, Uttar Pradesh",
        "2. Rampur, Himachal Pradesh",
        "3. Rampur, Odisha",
    ]


def test_a_grouped_list_is_cut_at_five_and_says_how_to_find_the_rest() -> None:
    """Seven Rampurs in seven states: list five, then tell the farmer whose
    place is not shown how to get to it."""

    states = ["UP", "HP", "OD", "BR", "JH", "MP", "RJ"]
    place = AmbiguousPlace(
        unresolved_name="Rampur",
        candidates=tuple(
            AreaMatch(
                name="Rampur",
                region="IN-XX",
                within=("India", state),
                geometry=Geometry(coordinates=[80.0, 26.0]),
            )
            for state in states
        ),
    )

    answer = answer_for_unplaced_asks((_ask(place=place),), _TEXT)

    assert answer is not None
    assert answer.content[0].text.splitlines() == [
        "Rampur is in several places. Which one:",
        "1. Rampur, UP",
        "2. Rampur, HP",
        "3. Rampur, OD",
        "4. Rampur, BR",
        "5. Rampur, JH",
        "Not in this list? Tell me the area it is in.",
    ]


def _ask(place=None) -> Ask:  # noqa: ANN001
    return Ask(
        subject_categories=SubjectCategory.WEATHER,
        interaction_type=InteractionType.OBSERVE,
        place=place,
    )


_PUNE = ResolvedPlace(
    name="Pune",
    within=("IN-MH",),
    geometry=Geometry(coordinates=[73.85, 18.52]),
    source=PlaceSource.NAMED,
)


def test_answer_for_unplaced_asks_is_none_if_every_ask_resolved() -> None:
    asks = (_ask(place=_PUNE),)

    assert answer_for_unplaced_asks(asks, _TEXT) is None


def test_answer_for_unplaced_asks_is_none_if_any_ask_resolved() -> None:
    """ "Weather in Pune and Xyzzy" — asking stops the turn, and Pune would go
    unanswered. Pune is answered; Xyzzy travels on as a failure instead."""

    unresolved = UnresolvedPlace(unresolved_name="Xyzzy")
    asks = (_ask(place=_PUNE), _ask(place=unresolved))

    assert answer_for_unplaced_asks(asks, _TEXT) is None


def test_answer_for_unplaced_asks_reports_every_distinct_failure() -> None:
    """Ambiguous and unresolved failures both reach the farmer in one reply —
    answering one should not require a second round-trip to hear the other."""

    ambiguous = AmbiguousPlace(unresolved_name="Bilaspur", candidates=())
    unresolved = UnresolvedPlace(unresolved_name="Xyzzy")
    asks = (_ask(place=ambiguous), _ask(place=unresolved))

    answer = answer_for_unplaced_asks(asks, _TEXT)

    assert answer is not None
    text = answer.content[0].text
    assert "Bilaspur" in text
    assert "Xyzzy" in text


def test_answer_for_unplaced_asks_shows_the_generic_question_once() -> None:
    """Every plain-`None` ask shares the identical question — asked once, not
    once per ask that needs it."""

    asks = (_ask(place=None), _ask(place=None))

    answer = answer_for_unplaced_asks(asks, _TEXT)

    assert answer is not None
    text = answer.content[0].text
    assert text.count(_TEXT.needs_place) == 1


def test_the_question_for_ambiguous_asks_skips_everything_that_resolved() -> None:
    """Pune was answered; only Bilaspur still needs the farmer's pick."""

    bilaspur = AmbiguousPlace(
        unresolved_name="Bilaspur",
        candidates=(
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
        ),
    )
    asks = (_ask(place=_PUNE), _ask(place=bilaspur))

    question = question_for_ambiguous_asks(asks, _TEXT)

    assert question == (
        "Which Bilaspur?\n1. Bilaspur, Himachal Pradesh\n2. Bilaspur, Chhattisgarh"
    )


def test_no_provider_names_the_place() -> None:
    """Nobody serves the ask here. Say so, rather than read as out of scope."""

    answer = answer_for_unserved_asks((_ask(place=_PUNE),), _TEXT)

    assert answer.content[0].text == (
        "I could not find a source that answers this for Pune yet."
    )


def test_no_provider_without_a_place_name() -> None:
    """A device point can arrive with no name to show."""

    device_point = _PUNE.model_copy(
        update={"name": "", "source": PlaceSource.ASSERTED_GEOMETRY}
    )

    answer = answer_for_unserved_asks((_ask(place=device_point),), _TEXT)

    assert answer.content[0].text == _TEXT.no_provider


def test_no_provider_names_each_place_once() -> None:
    """ "Wheat and onion price in Pune" is one thing the farmer can act on."""

    answer = answer_for_unserved_asks((_ask(place=_PUNE), _ask(place=_PUNE)), _TEXT)

    assert answer.content[0].text == (
        "I could not find a source that answers this for Pune yet."
    )
