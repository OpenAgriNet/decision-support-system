"""Tier 2 — the checksum validators a regex rule can name."""

from __future__ import annotations

import pytest

from dss.adapters.pii_identifier.regex.validators import VALIDATORS, is_valid


@pytest.mark.parametrize(
    ("validator", "value", "expected"),
    [
        # Verhoeff: "2363" is the published worked example.
        ("verhoeff", "2363", True),
        ("verhoeff", "2364", False),
        ("verhoeff", "234567890124", True),
        ("verhoeff", "234567890125", False),
        # Luhn: the classic worked example and a test card number.
        ("luhn", "79927398713", True),
        ("luhn", "4111111111111111", True),
        ("luhn", "4111111111111112", False),
        # GSTIN mod-36: a published example, then the same with a wrong check char.
        ("gstin", "27AAPFU0939F1ZV", True),
        ("gstin", "27AAPFU0939F1ZW", False),
        ("gstin", "27aapfu0939f1zv", True),
        # Format-only: the pattern already did the work.
        ("format", "anything", True),
    ],
)
def test_validator(validator: str, value: str, expected: bool) -> None:
    assert is_valid(validator, value) is expected


def test_checksum_ignores_separators() -> None:
    assert is_valid("verhoeff", "2345 6789 0124")
    assert is_valid("luhn", "4111-1111-1111-1111")


def test_every_named_validator_is_registered() -> None:
    assert set(VALIDATORS) == {"format", "verhoeff", "luhn", "gstin"}
