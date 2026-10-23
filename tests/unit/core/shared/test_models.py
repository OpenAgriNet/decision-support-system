from __future__ import annotations

import pytest

from dss.core.shared.language import ENGLISH
from dss.core.shared.models import TextQuery, TurnInput, UserTurn


@pytest.mark.parametrize(
    ("target_lang", "expected"), [("en", True), ("en-IN", True), ("gu", False)]
)
def test_is_target_lang_within_a_broader_tag(
    a_turn, target_lang: str, expected: bool
) -> None:
    assert a_turn(target_lang=target_lang).is_target_lang_within(ENGLISH) is expected


def test_turn_carries_its_text_as_input(a_turn) -> None:
    turn = a_turn(input=TextQuery(text="q"))

    assert turn.input.text == "q"


def test_turn_serialises_its_input_fields(a_turn) -> None:
    """A turn typed by the base input would otherwise dump an empty object."""
    envelope = a_turn().model_dump(exclude={"input"})
    turn = UserTurn[TurnInput](input=TextQuery(text="q"), **envelope)

    assert turn.model_dump()["input"] == {"text": "q"}
