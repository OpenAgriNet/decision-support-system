from __future__ import annotations

import pytest

from dss.core.shared.language import ENGLISH, LanguageTag
from dss.core.shared.models import UserTurn


def _turn(target_lang: str) -> UserTurn:
    return UserTurn(
        original_query="q",
        enriched_query="q",
        session_id="s1",
        transaction_id="t1",
        source_lang=LanguageTag(target_lang),
        target_lang=LanguageTag(target_lang),
        channel="web",
    )


@pytest.mark.parametrize(
    ("target_lang", "expected"), [("en", True), ("en-IN", True), ("gu", False)]
)
def test_is_target_lang_within_a_broader_tag(target_lang: str, expected: bool) -> None:
    assert _turn(target_lang).is_target_lang_within(ENGLISH) is expected
