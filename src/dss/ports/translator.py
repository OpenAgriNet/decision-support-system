"""The translator port: text from one language into another.

The language pair comes with each call, so one translator serves both ways.
``stream`` is ``def``, not ``async def``: an implementation is an async
generator.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from dss.core.shared.language import LanguageTag


@runtime_checkable
class Translator(Protocol):
    """Returns one result per text, in the same order. An adapter that can't
    stream yields its whole result once."""

    async def translate(
        self, texts: Sequence[str], *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> tuple[str, ...]: ...

    def stream(
        self, text: str, *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> AsyncIterator[str]: ...
