"""Tier 1 — loading the speed benchmark's question set.

The set is fixed so that two runs ask the same thing and can be compared.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from evals.perf.questions import load_questions

QUESTIONS = Path(__file__).parents[4] / "evals" / "perf" / "questions.toml"


def test_the_set_holds_ten_questions_per_category():
    questions = load_questions(QUESTIONS)

    assert Counter(q.category for q in questions) == {
        "mandi": 10,
        "weather": 10,
        "advisory": 10,
    }


def test_a_weather_question_carries_what_its_turn_sends():
    """Weather sends its point: the DSS does not pass a point it resolved by
    name on to /select, so without it every weather turn would miss."""

    akola = next(q for q in load_questions(QUESTIONS) if q.id == "37-1")

    assert akola.text == "Will it rain tomorrow in Akola district?"
    assert (akola.region, akola.area) == ("IN-MH", "Akola")
    assert akola.point == (77.056016, 20.748005)


def test_a_question_without_a_point_sends_none():
    """Mandi and advisory send the place name only, as a farmer typing would."""

    potato = next(q for q in load_questions(QUESTIONS) if q.id == "1-1")

    assert potato.point is None


def test_a_language_the_set_does_not_hold_fails_naming_the_ones_it_does():
    """Sending the English text under another language's name would time a
    turn nobody would ever send."""

    with pytest.raises(ValueError, match="'hi'.*known: en"):
        load_questions(QUESTIONS, lang="hi")
