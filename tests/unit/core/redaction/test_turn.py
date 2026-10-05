"""Tier 1 — taking a turn's texts out for redaction, and putting them back."""

from __future__ import annotations

from dss.core.redaction.turn import texts_of, with_texts
from dss.core.shared.models import ConversationMessage, UserTurn


def _turn() -> UserTurn:
    return UserTurn(
        original_query="my number is 9876543210",
        enriched_query="my number is 9876543210 (Pune)",
        session_id="0b6f3c1e-2f4a-4d7e-9a51-3c2d8e7f6a10",
        transaction_id="5d1e9a7c-8b3f-4c2a-b6d4-7e9f0a1b2c3d",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=[
            ConversationMessage(role="user", text="hi, I am Ramesh"),
            ConversationMessage(role="assistant", text="Hello Ramesh"),
        ],
    )


def test_texts_are_the_question_then_the_enriched_question_then_history() -> None:
    assert texts_of(_turn()) == [
        "my number is 9876543210",
        "my number is 9876543210 (Pune)",
        "hi, I am Ramesh",
        "Hello Ramesh",
    ]


def test_with_texts_puts_each_text_back_where_it_came_from() -> None:
    turn = with_texts(_turn(), ["q «phone_1»", "e «phone_1»", "u «person_1»", "a"])

    assert turn.original_query == "q «phone_1»"
    assert turn.enriched_query == "e «phone_1»"
    assert [(m.role, m.text) for m in turn.history] == [
        ("user", "u «person_1»"),
        ("assistant", "a"),
    ]


def test_with_texts_changes_nothing_else() -> None:
    before = _turn()
    after = with_texts(before, texts_of(before))

    assert after == before


def test_with_texts_refuses_a_count_that_does_not_match() -> None:
    try:
        with_texts(_turn(), ["only one"])
    except ValueError:
        return
    raise AssertionError("a short list must not silently drop history")
