"""One normalization rule, shared by the query side and the catalog side.

It has to be *one* rule: the adapter keys its index with it at boot and the
resolver looks up with it per turn, so two implementations that drift by a
character stop matching anything. Core owns it and the adapter imports it,
which is the direction the hexagonal rule allows.

Punctuation becomes a separator rather than being deleted, so ``PM-DDKY``,
``PM DDKY`` and ``pm ddky`` are one key. This is also why the catalog does not
need a spelling of every hyphenation.
"""

from __future__ import annotations

import unicodedata

# What may sit inside a token: letters and digits, plus combining marks.
#
# The marks are the reason this is a character scan rather than a `[\W_]+`
# split. `re`'s `\w` covers letters and digits but not Unicode marks, and a
# Devanagari vowel sign is a *spacing* mark (`Mc`) — so `\W` treated the
# matras in "मखाना" as separators and split one word into four fragments. The
# DSS is multilingual and a tenant may list Devanagari aliases, so tokens have
# to survive in any script the farmer types.
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
