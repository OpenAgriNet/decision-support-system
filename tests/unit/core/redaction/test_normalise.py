"""Tier 1 — the shadow copy that joins numbers people write with gaps."""

from __future__ import annotations

from dss.core.redaction.normalise import gap_pattern, join_number_gaps


def test_spaces_between_digits_are_joined() -> None:
    s = join_number_gaps("mera aadhaar 1234 5678 9012 hai")
    assert s.text == "mera aadhaar 123456789012 hai"


def test_dashes_between_digits_are_joined() -> None:
    assert join_number_gaps("98765-43210").text == "9876543210"


def test_two_separators_are_joined_but_three_are_not() -> None:
    assert join_number_gaps("98765 -43210").text == "9876543210"
    assert join_number_gaps("98765 - 43210").text == "98765 - 43210"


def test_separators_next_to_letters_are_kept() -> None:
    assert join_number_gaps("rate 2 acre - 5000").text == "rate 2 acre - 5000"


def test_span_maps_back_to_the_original() -> None:
    original = "no. 98765 43210 hai"
    s = join_number_gaps(original)
    start = s.text.index("9876543210")
    lo, hi = s.map_to_original(start, start + 10)
    assert original[lo:hi] == "98765 43210"


def test_nothing_to_join_is_an_identity_map() -> None:
    s = join_number_gaps("gehu ka rate")
    assert s.text == "gehu ka rate"
    assert s.map_to_original(0, 4) == (0, 4)


def test_joining_can_be_switched_off() -> None:
    assert join_number_gaps("98765 43210", gap=gap_pattern(max_separators=0)).text == (
        "98765 43210"
    )


def test_the_default_gap_pattern_is_built_once_at_import() -> None:
    from dss.core.redaction import normalise

    assert normalise.join_number_gaps.__kwdefaults__["gap"] is normalise.DEFAULT_GAP
