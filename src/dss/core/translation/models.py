from __future__ import annotations

from dataclasses import dataclass

from dss.core.shared.language import LanguageTag
from dss.ports.translator import Translator


@dataclass(frozen=True, slots=True)
class TranslationRoute:
    """How a farmer's language is served: the translator, and the language
    `to` the pipeline runs in."""

    translator: Translator
    to: LanguageTag
