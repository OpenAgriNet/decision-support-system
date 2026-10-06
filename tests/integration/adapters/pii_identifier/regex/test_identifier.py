"""Tier 2 — what each regex rule finds, and what it leaves alone."""

from __future__ import annotations

import asyncio

import pytest

from dss.adapters.pii_identifier.regex.identifier import RegexIdentifier
from dss.core.redaction.models import PiiSpan
from tests.support.redaction_rules import SETTINGS


def identify(text: str) -> list[PiiSpan]:
    [spans] = asyncio.run(RegexIdentifier(SETTINGS).identify([text]))
    return spans


def found(text: str) -> list[tuple[str, str]]:
    """(entity, the original text it covers) for every candidate."""

    return sorted({(c.entity, text[c.start : c.end]) for c in identify(text)})


@pytest.mark.parametrize(
    ("text", "entity", "span"),
    [
        ("mera aadhaar 234567890124 hai", "aadhaar", "234567890124"),
        ("mera aadhaar 2345 6789 0124 hai", "aadhaar", "2345 6789 0124"),
        ("aadhaar 2345-6789-0124", "aadhaar", "2345-6789-0124"),
        ("card 4111 1111 1111 1111 se", "card", "4111 1111 1111 1111"),
        ("gst 27AAPFU0939F1ZV", "gstin", "27AAPFU0939F1ZV"),
        ("pan ABCPE1234F hai", "pan", "ABCPE1234F"),
        ("pan abcpe1234f hai", "pan", "abcpe1234f"),
        ("ifsc SBIN0001234", "ifsc", "SBIN0001234"),
        ("mera number 9876543210 hai", "phone", "9876543210"),
        ("mera number 98765 43210 hai", "phone", "98765 43210"),
        ("call 98765-43210", "phone", "98765-43210"),
        ("call +91 98765 43210", "phone", "+91 98765 43210"),
        ("mail ramesh.p@example.co.in", "email", "ramesh.p@example.co.in"),
    ],
)
def test_each_identifier_is_found(text: str, entity: str, span: str) -> None:
    assert found(text) == [(entity, span)]


def test_a_twelve_digit_number_that_fails_verhoeff_is_not_aadhaar() -> None:
    assert found("order no 234567890125") == []


def test_a_card_number_that_fails_luhn_is_not_a_card() -> None:
    assert found("ref 4111111111111112") == []


def test_a_gstin_with_a_wrong_check_character_is_not_found() -> None:
    assert found("gst 27AAPFU0939F1ZW") == []


def test_a_phone_followed_by_another_number_is_still_found() -> None:
    # The shadow copy joins "9876543210 2" into eleven digits; the original
    # text still carries the phone on its own.
    assert found("number 9876543210 2 acre") == [("phone", "9876543210")]


@pytest.mark.parametrize(
    "text",
    [
        "gehu ka rate kya hai?",
        "champa ka rate kya hai?",
        "2 acre mein 5000 kg",
        "mandi rate 2150 per quintal",
        "rate 2000 3000 ke beech",
    ],
)
def test_ordinary_questions_are_left_alone(text: str) -> None:
    assert found(text) == []


def test_a_declared_name_is_found() -> None:
    assert found("my name is Ramesh Patel and I grow wheat") == [
        ("person", "Ramesh Patel")
    ]


def test_a_declared_name_is_found_in_lower_case() -> None:
    assert found("hello, my name is ramesh from nashik") == [("person", "ramesh")]


def test_a_declared_name_stops_at_three_tokens() -> None:
    assert found("my name is Ram Kumar Singh Yadav") == [("person", "Ram Kumar Singh")]


def test_a_phrase_with_no_name_after_it_finds_nothing() -> None:
    assert found("my name is") == []
    assert found("my name is 123") == []


def test_a_name_mentioned_in_passing_is_not_found() -> None:
    # Accepted for #133; #136 catches these.
    assert found("Ramesh ke khet mein paani nahi hai") == []


def test_candidates_carry_the_canonical_value() -> None:
    [candidate] = identify("call +91 98765 43210")
    assert candidate.value == "+919876543210"
    assert candidate.score == 1.0


def test_every_span_carries_its_value() -> None:
    # Keep or destroy is the policy's call, in core; the identifier always
    # reports the value.
    [candidate] = identify("aadhaar 2345 6789 0124")
    assert candidate.value == "234567890124"


def test_patterns_are_compiled_once_when_the_identifier_is_built(
    monkeypatch,
) -> None:
    identifier = RegexIdentifier(SETTINGS)

    def no_compile(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("compiled on a turn")

    monkeypatch.setattr(
        "dss.adapters.pii_identifier.regex.identifier.re.compile", no_compile
    )
    asyncio.run(identifier.identify(["call 98765 43210, my name is Ramesh"]))


def test_an_email_keeps_its_dashes_between_digits() -> None:
    # Gap joining is for numbers. An email is kept exactly as written.
    [candidate] = identify("mail kisan-2024-25@gmail.com")
    assert candidate.entity == "email"
    assert candidate.value == "kisan-2024-25@gmail.com"


def test_a_phone_glued_to_the_next_number_is_not_read_as_a_card() -> None:
    # "9876543210102" passes the card check. The two numbers were written
    # apart, so the phone stays a phone and "102" stays in the question.
    assert found("mera number 9876543210 102 kg") == [("phone", "9876543210")]


def test_a_phone_with_its_prefix_written_apart_keeps_the_prefix() -> None:
    # The joined copy reads "0091 9876543210" as one longer phone. That is the
    # same number growing, not two numbers glued, so the longer one stands.
    assert ("phone", "0091 9876543210") in found("call 0091 9876543210")
