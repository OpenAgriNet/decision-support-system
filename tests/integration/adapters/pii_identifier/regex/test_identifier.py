"""Tier 2 — what each regex rule finds, and what it leaves alone."""

from __future__ import annotations

import asyncio

import pytest

from dss.adapters.pii_identifier.regex.identifier import RegexIdentifier
from dss.adapters.pii_identifier.regex.models import PatternRule, RegexSettings
from dss.core.redaction.models import PiiSpan
from dss.ports.pii_identifier import IdentifierUnavailable
from tests.support.redaction_rules import SETTINGS


def identify(text: str) -> list[PiiSpan]:
    spans = asyncio.run(RegexIdentifier(SETTINGS).identify(text))
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


def test_every_turn_reuses_the_patterns_built_at_startup() -> None:
    identifier = RegexIdentifier(SETTINGS)
    rules, gap = list(identifier._compiled), identifier._gap

    for _ in range(2):
        asyncio.run(identifier.identify("call 98765 43210, my name is Ramesh"))

    assert all(a is b for a, b in zip(identifier._compiled, rules, strict=True))
    assert identifier._gap is gap


def test_a_pattern_that_cannot_compile_stops_the_build_and_names_the_rule() -> None:
    # The rules file check catches this first; the build is the last guard.
    broken = PatternRule.model_construct(
        entity="phone", kind="pattern", pattern="[6-9", validator="format"
    )
    settings = RegexSettings.model_construct(
        type="regex", normalisation=SETTINGS.normalisation, rules=[broken]
    )

    with pytest.raises(IdentifierUnavailable, match="rule 'phone'"):
        RegexIdentifier(settings)


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


def test_a_phone_with_a_0091_prefix_is_not_read_as_a_card() -> None:
    # "00916000000007" passes the card check. No card number starts with 0.
    assert found("call 0091 6000000007") == [
        ("phone", "0091 6000000007"),
        ("phone", "6000000007"),
    ]


def test_a_title_before_a_declared_name_is_left_out() -> None:
    assert found("my name is Dr. Ramesh Patel") == [("person", "Ramesh Patel")]


def test_a_colon_after_the_phrase_does_not_hide_the_name() -> None:
    assert found("my name is: Ramesh Patel") == [("person", "Ramesh Patel")]


def test_a_name_ends_at_a_comma() -> None:
    assert found("my name is Ramesh, onion rate?") == [("person", "Ramesh")]


# --- groupings: a joined number must be written in its real groups ----------


def test_a_phone_written_with_a_gap_is_not_read_as_a_card() -> None:
    # "98765 43210 102" joins into 13 digits that can pass the card check.
    # Groups of 5-5-3 are not how a card is written, so it stays a phone.
    assert found("mera number 98765 43210 102 kg") == [("phone", "98765 43210")]


def test_a_card_in_its_real_groups_is_still_found() -> None:
    assert found("card 4111 1111 1111 1111") == [("card", "4111 1111 1111 1111")]


def test_an_aadhaar_in_its_real_groups_is_still_found() -> None:
    assert found("aadhaar 2345 6789 0124") == [("aadhaar", "2345 6789 0124")]


def test_an_aadhaar_in_other_groups_is_not_joined() -> None:
    assert ("aadhaar", "23456 7890 124") not in found("ids 23456 7890 124")


def test_a_number_written_whole_ignores_groupings() -> None:
    assert found("card 4111111111111111") == [("card", "4111111111111111")]


@pytest.mark.parametrize(
    ("text", "entity", "span"),
    [
        ("pm kisan no UP123456789 hai", "pm_kisan_id", "UP123456789"),
        ("pm kisan no up123456789 hai", "pm_kisan_id", "up123456789"),
        ("farmer id 12345678901 hai", "farmer_id", "12345678901"),
        ("farm id MH123456789012 hai", "farm_id", "MH123456789012"),
        ("farm id mh123456789012 hai", "farm_id", "mh123456789012"),
    ],
)
def test_each_agri_identifier_is_found(text: str, entity: str, span: str) -> None:
    assert found(text) == [(entity, span)]


@pytest.mark.parametrize(
    "text",
    [
        "UP12345678",  # PM Kisan one digit short
        "UP1234567890",  # PM Kisan one digit long
        "U1234567890",  # one letter, ten digits
        "1234567890",  # ten digits
        "MH12345678901",  # farm ID one digit short: no farmer ID hiding inside
        "MH1234567890123",  # farm ID one digit long
        "MHX12345678901",  # three letters
    ],
)
def test_agri_identifiers_of_the_wrong_shape_are_not_found(text: str) -> None:
    assert found(f"id {text} hai") == []


def test_a_phone_glued_to_one_more_digit_is_not_a_farmer_id() -> None:
    # Joined, "9876543210 2" is eleven digits; a farmer ID is written whole.
    assert found("number 9876543210 2 acre") == [("phone", "9876543210")]


def test_a_farmer_id_written_with_gaps_is_not_joined() -> None:
    assert found("id 12345 678901 hai") == []
