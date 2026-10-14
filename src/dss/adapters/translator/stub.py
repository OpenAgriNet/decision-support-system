"""STUB — a translator that answers from two rules, not a model.

What `adapters/llm/stub.py` is to the pipeline: it lets the whole frame run
before a translation model exists, and stays for local runs. Selected only by
`adapter: stub` in the translation config. Never in a deployment.

No network, no model. Any text in its own language becomes one canned
question, which the mock network can answer. Anything else comes back marked
with the language it was sent to, so the way out is visible too. It records
what it was asked so a test can assert the direction of each call.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Final

from dss.core.shared.language import LanguageTag

# The mock network answers weather for the turn's coordinates.
CANNED_QUESTION: Final = "Will it rain today?"

_WORD_AND_TRAILING_SPACE = re.compile(r"\S+\s*")


@dataclass(frozen=True)
class TranslationCall:
    """One recorded request. A stream is a request for one text."""

    texts: tuple[str, ...]
    from_lang: LanguageTag
    to_lang: LanguageTag


class StubTranslator:
    def __init__(self, *, language: str) -> None:
        self._language = language
        self.calls: list[TranslationCall] = []

    async def translate(
        self, texts: Sequence[str], *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> tuple[str, ...]:
        self.calls.append(TranslationCall(tuple(texts), from_lang, to_lang))
        return tuple(
            self._translate_text(text, from_lang=from_lang, to_lang=to_lang)
            for text in texts
        )

    async def stream(
        self, text: str, *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> AsyncIterator[str]:
        """Yield the `translate` result a word at a time."""

        self.calls.append(TranslationCall((text,), from_lang, to_lang))
        translated = self._translate_text(text, from_lang=from_lang, to_lang=to_lang)
        for piece in _WORD_AND_TRAILING_SPACE.findall(translated):
            yield piece

    def _translate_text(
        self, text: str, *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> str:
        if self._is_from_the_farmer(from_lang):
            return CANNED_QUESTION
        return f"[{to_lang}] {text}"

    def _is_from_the_farmer(self, from_lang: LanguageTag) -> bool:
        return from_lang.primary == self._language
