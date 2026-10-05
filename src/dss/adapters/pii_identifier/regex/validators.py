"""The checks a rule can name to confirm a pattern match is real.

A rule names its validator in config; the validators themselves are code. A
twelve-digit number is only an Aadhaar if its Verhoeff check digit is right, so a
phone bill or an order number of the same length is left alone.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]")

# Verhoeff tables: the dihedral group D5 and its position permutations.
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _verhoeff(value: str) -> bool:
    check = 0
    for i, digit in enumerate(reversed(value)):
        check = _D[check][_P[i % 8][int(digit)]]
    return check == 0


def _luhn(value: str) -> bool:
    total = 0
    for i, digit in enumerate(reversed(value)):
        n = int(digit)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _gstin(value: str) -> bool:
    value = value.upper()
    total = 0
    for i, char in enumerate(value[:14]):
        product = _BASE36.index(char) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return value[14] == _BASE36[(36 - total % 36) % 36]


def _digits(check: Callable[[str], bool]) -> Callable[[str], bool]:
    return lambda value: value.isdigit() and check(value)


def _format(_value: str) -> bool:
    return True


VALIDATORS: dict[str, Callable[[str], bool]] = {
    "format": _format,
    "verhoeff": _digits(_verhoeff),
    "luhn": _digits(_luhn),
    "gstin": lambda value: len(value) == 15 and _gstin(value),
}


def is_valid(validator: str, value: str) -> bool:
    """Whether ``value`` passes ``validator``. Spaces and dashes are ignored."""

    return VALIDATORS[validator](_NOT_ALNUM.sub("", value))
