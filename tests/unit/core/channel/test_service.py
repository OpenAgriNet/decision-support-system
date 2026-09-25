"""Tier 1 — the deterministic channel shaping.

The prose-writing composer lives in `core/channel/compose.py` (it needs an
LLM); what stays here is the fixed no-match reply. `answer_from_evidence` has
its own test alongside this one.
"""

from __future__ import annotations

from dss.core.channel.models import ClarificationText
from dss.core.channel.service import (
    NO_MATCH_TEXT,
    ambiguous_place_answer,
    answer_for_unplaced_asks,
    needs_place_answer,
    no_match_answer,
    unknown_place_answer,
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
    ambiguous_place_header="Which {name}?",
)


def test_no_match_answer_has_one_block_and_no_sources() -> None:
    answer = no_match_answer()

    assert len(answer.content) == 1
    assert answer.content[0].text == NO_MATCH_TEXT
    # nothing was consulted, so nothing is cited
    assert answer.sources == ()
    assert answer.content[0].source_ids == ()


def test_needs_place_answer_asks_where_not_which_district() -> None:
    """Blocks mean a smaller place than a district is now answerable, so
    demanding a district specifically is both wrong and needlessly narrow."""

    answer = needs_place_answer(_TEXT)

    assert len(answer.content) == 1
    assert answer.content[0].text == _TEXT.needs_place
    assert "district" not in _TEXT.needs_place.lower()
    assert answer.sources == ()
    assert answer.content[0].source_ids == ()


def test_unknown_place_answer_names_the_place() -> None:
    """A place the index does not carry is a different problem from naming
    nowhere — the farmer already answered, just with a name we don't have."""

    answer = unknown_place_answer("Xyzzy", _TEXT)

    assert len(answer.content) == 1
    assert "Xyzzy" in answer.content[0].text
    assert answer.sources == ()


def test_ambiguous_place_answer_numbers_each_candidate() -> None:
    """A stable numbered list lets a later turn read the candidates back from
    history and pick by number, so nothing has to be invented."""

    candidates = (
        AreaMatch(
            name="Bilaspur",
            region="IN-HP",
            geometry=Geometry(coordinates=[76.75, 31.33]),
        ),
        AreaMatch(
            name="Bilaspur",
            region="IN-CT",
            geometry=Geometry(coordinates=[82.15, 22.09]),
        ),
    )

    answer = ambiguous_place_answer("Bilaspur", candidates, _TEXT)

    text = answer.content[0].text
    assert "Bilaspur" in text
    assert "1. Bilaspur, IN-HP" in text
    assert "2. Bilaspur, IN-CT" in text
    assert answer.sources == ()


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


def test_answer_for_unplaced_asks_skips_resolved_asks() -> None:
    """One ask resolved, one didn't — only the unresolved one's problem is
    surfaced; the resolved one proceeds silently."""

    unresolved = UnresolvedPlace(unresolved_name="Xyzzy")
    asks = (_ask(place=_PUNE), _ask(place=unresolved))

    answer = answer_for_unplaced_asks(asks, _TEXT)

    assert answer is not None
    assert "Pune" not in answer.content[0].text
    assert "Xyzzy" in answer.content[0].text


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
