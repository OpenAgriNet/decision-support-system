"""One normalization rule, shared by the query side and the catalog side.

Two implementations that drift by a character stop matching, so core owns it
and the adapter imports it. Punctuation separates rather than deletes, so
``PM-DDKY``, ``PM DDKY`` and ``pm ddky`` are one key.
"""

from __future__ import annotations

import unicodedata

# What may sit inside a token: letters, digits, and combining marks. The marks
# are why this is a character scan and not a `[\W_]+` split — `\w` misses
# Unicode marks, and a Devanagari vowel sign is a spacing mark, so the regex
# shredded "मखाना" into four fragments.
_MARK = "M"


def _is_token_character(character: str) -> bool:
    return character.isalnum() or unicodedata.category(character).startswith(_MARK)


def normalize_tokens(text: str) -> tuple[str, ...]:
    """The comparable tokens of ``text``, casefolded, punctuation stripped."""

    tokens: list[str] = []
    current: list[str] = []
    for character in text.casefold():
        if _is_token_character(character):
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def alias_key(text: str) -> str:
    """The single string an alias is indexed and looked up by."""

    return " ".join(normalize_tokens(text))
