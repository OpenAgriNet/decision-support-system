"""Tier 1 — redacting a turn's texts: tags, the reveal map, and the counts."""

from __future__ import annotations

from dss.core.redaction.models import Candidate
from dss.core.redaction.service import redact
from tests.support.redaction_rules import CONFIG


def test_aadhaar_is_replaced_and_not_kept() -> None:
    result = redact(["mera aadhaar 2345 6789 0124 hai, gehu ka rate kya hai?"], CONFIG)
    assert result.texts == ("mera aadhaar «aadhaar_1» hai, gehu ka rate kya hai?",)
    assert result.reveal.values == {}
    assert result.found == {"aadhaar": 1}


def test_phone_is_replaced_and_kept_for_the_provider() -> None:
    result = redact(["mera number 98765 43210 hai"], CONFIG)
    assert result.texts == ("mera number «phone_1» hai",)
    assert result.reveal.values == {"«phone_1»": "9876543210"}


def test_an_ordinary_question_is_unchanged() -> None:
    result = redact(["champa ka rate kya hai?"], CONFIG)
    assert result.texts == ("champa ka rate kya hai?",)
    assert result.found == {}


def test_the_same_value_gets_the_same_tag_across_texts() -> None:
    result = redact(
        ["my number is 9876543210", "call 98765 43210 please"],
        CONFIG,
    )
    assert result.texts == ("my number is «phone_1»", "call «phone_1» please")
    assert result.found == {"phone": 2}


def test_different_values_are_numbered_in_order() -> None:
    result = redact(["9876543210 ya 9123456789"], CONFIG)
    assert result.texts == ("«phone_1» ya «phone_2»",)
    assert result.reveal.values == {
        "«phone_1»": "9876543210",
        "«phone_2»": "9123456789",
    }


def test_destroyed_values_are_numbered_too() -> None:
    result = redact(["234567890124 aur 987654321012"], CONFIG)
    assert result.texts == ("«aadhaar_1» aur «aadhaar_2»",)
    assert result.reveal.values == {}


def test_several_entities_in_one_text() -> None:
    result = redact(["my name is Ramesh Patel, 9876543210, ramesh@example.com"], CONFIG)
    assert result.texts == ("my name is «person_1», «phone_1», «email_1»",)
    assert result.found == {"person": 1, "phone": 1, "email": 1}


def test_the_longest_overlapping_candidate_wins() -> None:
    # A phone inside a longer extra candidate: the longer span is kept.
    text = "call 9876543210 now"
    extra = Candidate(start=0, end=15, entity="note", score=0.9, source="test")
    result = redact([text], CONFIG, extra=[[extra]])
    assert result.texts == ("«note_1» now",)


def test_an_extra_candidate_is_merged_with_the_patterns() -> None:
    # The seam #136 plugs a model into: candidates from elsewhere, same pass.
    text = "Ramesh ke khet mein, number 9876543210"
    extra = Candidate(
        start=0, end=6, entity="person", score=0.8, source="ner", value="Ramesh"
    )
    result = redact([text], CONFIG, extra=[[extra]])
    assert result.texts == ("«person_1» ke khet mein, number «phone_1»",)
    assert result.reveal.values["«person_1»"] == "Ramesh"


def test_empty_input() -> None:
    result = redact([], CONFIG)
    assert result.texts == ()
    assert result.found == {}
