"""Loads the edge translation config (#174). Off unless
``DSS_EDGE_TRANSLATION_CONFIG_PATH`` is set; a bad file stops the boot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dss.core.shared.language import LanguageTag


class StubBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    to: LanguageTag
    adapter: Literal["stub"]


class LLMBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    to: LanguageTag
    adapter: Literal["llm"]
    # A gateway name, or a Pydantic AI model string, like `DSS_*_MODEL`.
    model: str = Field(min_length=1)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(20.0, gt=0.0)


type TranslationBinding = Annotated[
    StubBinding | LLMBinding, Field(discriminator="adapter")
]


class EdgeTranslationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    languages: dict[str, TranslationBinding]

    @field_validator("languages")
    @classmethod
    def _check_keys_are_language_subtags(
        cls, languages: dict[str, TranslationBinding]
    ) -> dict[str, TranslationBinding]:
        bad_keys = [key for key in languages if not _is_language_subtag(key)]
        if bad_keys:
            raise ValueError(
                f"{', '.join(bad_keys)}: a key must be a lowercase language "
                "subtag such as 'gu', with no region or script"
            )
        return languages

    # A route into a translated language would need a second translation the
    # orchestrator never runs; into its own language, it would translate nothing.
    @field_validator("languages")
    @classmethod
    def _check_each_route_ends_in_a_served_language(
        cls, languages: dict[str, TranslationBinding]
    ) -> dict[str, TranslationBinding]:
        looped_or_chained = [
            f"{language} -> {binding.to}"
            for language, binding in languages.items()
            if binding.to.primary in languages
        ]
        if looped_or_chained:
            raise ValueError(
                f"{', '.join(looped_or_chained)}: `to` must be a language that "
                "is not translated itself"
            )
        return languages


def load_edge_translation_config(path: Path | None) -> dict[str, TranslationBinding]:
    """Each translated language's binding by its subtag; empty when off."""

    if path is None:
        return {}
    if not path.exists():
        raise FileNotFoundError(
            f"edge translation config path is set to {path} but no file is there — "
            "refusing to boot without it"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return EdgeTranslationConfig.model_validate(data).languages


def _is_language_subtag(key: str) -> bool:
    try:
        return LanguageTag(key).primary == key
    except ValueError:
        return False
