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
    """STUB: no model, for local runs. Text in its own language becomes
    `CANNED_QUESTION`; anything else comes back marked with its target language."""

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
