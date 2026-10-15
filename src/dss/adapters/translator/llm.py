"""A translator backed by a language model (#174).

A turn's texts go in one structured call, a JSON list in and a list out, so the
history and the question are translated together. An answer goes out streamed,
as the model writes it. The system prompt comes from the prompt registry,
always in English: it instructs the model, whatever language the text is in.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

from pydantic import BaseModel, ConfigDict

from dss.core.shared.language import ENGLISH, LanguageTag
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptIdentifier, PromptProvider


class TranslatedTexts(BaseModel):
    """What the model returns for a batch: one text per text sent, in order."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    texts: list[str]


class LLMTranslator:
    def __init__(self, *, llm: LLMProvider, prompts: PromptProvider) -> None:
        self._llm = llm
        self._prompts = prompts

    async def translate(
        self, texts: Sequence[str], *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> tuple[str, ...]:
        result = await self._llm.structured(
            system_prompt=self._render_system_prompt(from_lang, to_lang),
            user_query=json.dumps(list(texts), ensure_ascii=False),
            schema=TranslatedTexts,
        )
        return tuple(result.texts)

    def stream(
        self, text: str, *, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> AsyncIterator[str]:
        """The model's own stream, handed back rather than relayed, so closing
        it closes the model's call."""

        return self._llm.stream_text(
            system_prompt=self._render_system_prompt(from_lang, to_lang),
            user_query=text,
        )

    def _render_system_prompt(
        self, from_lang: LanguageTag, to_lang: LanguageTag
    ) -> str:
        return self._prompts.get_prompt(
            PromptIdentifier.TRANSLATOR,
            lang=ENGLISH,
            placeholder_values={
                "translate_from": str(from_lang),
                "translate_to": str(to_lang),
            },
        )
