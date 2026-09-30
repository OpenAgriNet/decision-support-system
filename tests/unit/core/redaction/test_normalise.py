"""Tier 1 — the shadow copy that joins numbers people write with gaps."""

from __future__ import annotations

from dss.core.redaction.models import Normalise
from dss.core.redaction.normalise import shadow

DEFAULT = Normalise()


def test_spaces_between_digits_are_joined() -> None:
    s = shadow("mera aadhaar 1234 5678 9012 hai", DEFAULT)
    assert s.text == "mera aadhaar 123456789012 hai"


def test_dashes_between_digits_are_joined() -> None:
    assert shadow("98765-43210", DEFAULT).text == "9876543210"


def test_two_separators_are_joined_but_three_are_not() -> None:
    assert shadow("98765 -43210", DEFAULT).text == "9876543210"
    assert shadow("98765 - 43210", DEFAULT).text == "98765 - 43210"


def test_separators_next_to_letters_are_kept() -> None:
    assert shadow("rate 2 acre - 5000", DEFAULT).text == "rate 2 acre - 5000"


def test_span_maps_back_to_the_original() -> None:
    original = "no. 98765 43210 hai"
    s = shadow(original, DEFAULT)
    start = s.text.index("9876543210")
    lo, hi = s.to_original(start, start + 10)
    assert original[lo:hi] == "98765 43210"


def test_nothing_to_join_is_an_identity_map() -> None:
    s = shadow("gehu ka rate", DEFAULT)
    assert s.text == "gehu ka rate"
    assert s.to_original(0, 4) == (0, 4)


def test_joining_can_be_switched_off() -> None:
    off = Normalise(max_separators=0)
    assert shadow("98765 43210", off).text == "98765 43210"
