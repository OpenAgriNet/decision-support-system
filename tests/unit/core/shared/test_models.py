from __future__ import annotations

import pytest

from dss.core.shared.language import ENGLISH, LanguageTag
from dss.core.shared.models import TextQuery


@pytest.mark.parametrize(
    ("target_lang", "expected"), [("en", True), ("en-IN", True), ("gu", False)]
)
def test_is_target_lang_within_a_broader_tag(
    a_turn, target_lang: str, expected: bool
) -> None:
    turn = a_turn(
        source_lang=LanguageTag(target_lang), target_lang=LanguageTag(target_lang)
    )

    assert turn.is_target_lang_within(ENGLISH) is expected


def test_turn_carries_its_text_as_input(a_turn) -> None:
    turn = a_turn(input=TextQuery(text="q"))

    assert turn.input.text == "q"
