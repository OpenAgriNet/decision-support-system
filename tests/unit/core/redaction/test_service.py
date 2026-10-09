"""Tier 1 — redacting from identified spans: tags, the value map, the counts.

The spans are written by hand. How they were found — regex, a model, a service —
is not core's business.
"""

from __future__ import annotations

from dss.core.redaction.models import PiiSpan, RedactionPolicy, ValueHandling
from dss.core.redaction.service import redact

POLICY = RedactionPolicy(
    entities={
        "phone": ValueHandling.KEEP,
        "person": ValueHandling.KEEP,
        "aadhaar": ValueHandling.DESTROY,
    }
)


def span(text: str, part: str, entity: str, value: str | None = None) -> PiiSpan:
    start = text.index(part)
    return PiiSpan(
        start=start,
        end=start + len(part),
        entity=entity,
        score=1.0,
        source="test",
        value=value or part,
    )


def test_a_kept_entity_is_tagged_and_held() -> None:
    text = "mera number 98765 43210 hai"
    spans = [[span(text, "98765 43210", "phone", "9876543210")]]
    result = redact([text], spans, POLICY)
    assert result.texts == ("mera number «phone_1» hai",)
    assert result.visibility.values == {"«phone_1»": "9876543210"}
    assert result.found == {"phone": 1}


def test_a_destroyed_entity_is_tagged_and_not_held() -> None:
    text = "aadhaar 2345 6789 0124"
    result = redact([text], [[span(text, "2345 6789 0124", "aadhaar")]], POLICY)
    assert result.texts == ("aadhaar «aadhaar_1»",)
    assert result.visibility.values == {}


def test_an_entity_missing_from_the_policy_is_destroyed() -> None:
    text = "voter id ABC1234567"
    result = redact([text], [[span(text, "ABC1234567", "voter_id")]], POLICY)
    assert result.texts == ("voter id «voter_id_1»",)
    assert result.visibility.values == {}


def test_no_spans_leave_the_texts_unchanged() -> None:
    result = redact(["gehu ka rate?", "aur chana?"], [], POLICY)
    assert result.texts == ("gehu ka rate?", "aur chana?")
    assert result.found == {}
    assert result.visibility.values == {}


def test_the_same_value_gets_the_same_tag_across_texts() -> None:
    a, b = "my number is 9876543210", "call 98765 43210 please"
    result = redact(
        [a, b],
        [[span(a, "9876543210", "phone")], [span(b, "98765 43210", "phone")]],
        POLICY,
    )
    assert result.texts == ("my number is «phone_1»", "call «phone_1» please")
    assert result.found == {"phone": 2}


def test_different_values_are_numbered_in_order() -> None:
    text = "9876543210 ya 9123456789"
    spans = [[span(text, "9876543210", "phone"), span(text, "9123456789", "phone")]]
    assert redact([text], spans, POLICY).texts == ("«phone_1» ya «phone_2»",)


def test_destroyed_values_are_numbered_too() -> None:
    text = "234567890124 aur 987654321012"
    spans = [
        [span(text, "234567890124", "aadhaar"), span(text, "987654321012", "aadhaar")]
    ]
    assert redact([text], spans, POLICY).texts == ("«aadhaar_1» aur «aadhaar_2»",)


def test_spans_from_several_identifiers_are_resolved_together() -> None:
    # A phone from one identifier, a longer span over it from another: the
    # longer one wins.
    text = "call 9876543210 now"
    phone = span(text, "9876543210", "phone")
    note = PiiSpan(0, 15, "note", 0.9, "other", "call 9876543210")
    assert redact([text], [[phone, note]], POLICY).texts == ("«note_1» now",)


def test_equal_spans_go_to_the_one_listed_first() -> None:
    text = "Ramesh ji"
    first = span(text, "Ramesh", "person")
    second = PiiSpan(0, 6, "place", 0.9, "other", "Ramesh")
    assert redact([text], [[first, second]], POLICY).texts == ("«person_1» ji",)


def test_empty_input() -> None:
    result = redact([], [], POLICY)
    assert result.texts == ()
    assert result.failed == ()


def test_a_number_with_and_without_its_country_code_gets_one_tag() -> None:
    # The farmer gave "+91 98765 43210" once and "9876543210" later. Same phone.
    a, b = "call +91 98765 43210", "my number is 9876543210"
    result = redact(
        [a, b],
        [
            [span(a, "+91 98765 43210", "phone", "+919876543210")],
            [span(b, "9876543210", "phone")],
        ],
        POLICY,
    )
    assert result.texts == ("call «phone_1»", "my number is «phone_1»")
    # The first form written is the one held.
    assert result.visibility.values == {"«phone_1»": "+919876543210"}
