"""Tier 1 — swapping tags back for the provider, and hiding values it echoes."""

from __future__ import annotations

from dss.core.redaction.models import Normalise
from dss.core.redaction.reveal import RevealMap

MAP = RevealMap(
    values={"«phone_1»": "9876543210", "«person_1»": "Ramesh"},
    normalise=Normalise(),
)


def test_reveal_swaps_known_tags_anywhere_in_the_body() -> None:
    body = {
        "applicant": {"phone": "«phone_1»", "name": "«person_1»"},
        "notes": ["call «phone_1» after 5"],
        "count": 2,
    }
    assert MAP.reveal(body) == {
        "applicant": {"phone": "9876543210", "name": "Ramesh"},
        "notes": ["call 9876543210 after 5"],
        "count": 2,
    }


def test_reveal_leaves_a_tag_it_does_not_hold() -> None:
    # A destroyed entity has no entry, so its tag goes out as the tag.
    assert MAP.reveal({"id": "«aadhaar_1»"}) == {"id": "«aadhaar_1»"}


def test_reveal_does_not_change_the_input() -> None:
    body = {"phone": "«phone_1»"}
    MAP.reveal(body)
    assert body == {"phone": "«phone_1»"}


def test_conceal_hides_an_echoed_value() -> None:
    assert (
        MAP.conceal("status for 9876543210: approved")
        == "status for «phone_1»: approved"
    )


def test_conceal_hides_an_echo_written_with_gaps() -> None:
    assert MAP.conceal("mobile 98765 43210 registered") == "mobile «phone_1» registered"


def test_conceal_leaves_other_numbers_alone() -> None:
    # A KVK officer's number is information the farmer asked for.
    assert MAP.conceal("call KVK at 9123456789") == "call KVK at 9123456789"


def test_conceal_matches_whole_words_only() -> None:
    assert MAP.conceal("Rameshwar mandi") == "Rameshwar mandi"
    assert MAP.conceal("ramesh ji") == "«person_1» ji"


def test_an_empty_map_changes_nothing() -> None:
    empty = RevealMap(values={}, normalise=Normalise())
    assert empty.reveal({"a": "«phone_1»"}) == {"a": "«phone_1»"}
    assert empty.conceal("9876543210") == "9876543210"
