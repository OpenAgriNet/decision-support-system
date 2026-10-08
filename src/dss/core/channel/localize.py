"""Rendering system-authored text in the farmer's language.

The DSS writes some replies itself, with no model in the loop: moderation
refusals (``core/moderation/messages.py``), clarification questions and the
no-provider answers (``core/channel/service.py``, wording in
``config/defaults/clarification-text.yaml``). All of that text is English.
This module is the one seam that turns it into the turn's ``target_lang``
before the farmer reads it — one short model call on the composer's binding,
asked to render the message and change nothing else (ADR-0018).

The rules the mechanism rests on:

- **English costs nothing.** A turn whose target language is ``en`` (any
  ``en-*``) returns the text untouched, with no model call — these paths were
  deliberately model-free and stay that way for English.
- **A localization failure never costs the answer.** If the model call fails,
  the farmer gets the English text — worse than their language, far better
  than no reply. The adapter's own logging records the failure.
- **Code-emitted tokens stay verbatim.** The ambiguous-place question's
  numbered options are copied back by a later turn and looked up in the
  English area index (ADR-0017), so the localizer's prompt
  (``prompts/localizer/``) orders place names, numbers, scheme names and the
  option lines kept exactly as written — only the sentence around them moves.

``structured`` rather than ``stream_text``: the output is one short field, the
schema stops the model wrapping it in preamble, and the adapter's retries
apply — a streamed call has none.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from dss.core.channel.models import ComposedAnswer
from dss.core.shared.models import UserTurn
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider


class LocalizedText(BaseModel):
    """The one field the localizer model returns: the same message, rendered
    in the target language."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str


class LocalizeText(Protocol):
    """A per-turn text localizer, already bound to its model and prompt
    source in the composition root."""

    async def __call__(self, text: str, *, turn: UserTurn) -> str: ...


def _is_english(target_lang: str) -> bool:
    return target_lang.lower().split("-", 1)[0] == "en"


def build_localizer(*, llm: LLMProvider, prompts: PromptProvider) -> LocalizeText:
    """Bind the model and the prompt source once; return the per-turn callable,
    so the composition root is the only place that names either."""

    async def localize(text: str, *, turn: UserTurn) -> str:
        if _is_english(turn.target_lang) or not text.strip():
            return text
        try:
            rendered = await llm.structured(
                system_prompt=prompts.get_prompt(
                    "LOCALIZER",
                    lang=turn.target_lang,
                    kwargs={"target_lang": turn.target_lang},
                ),
                user_query=text,
                schema=LocalizedText,
            )
        except Exception:  # noqa: BLE001 - any failure here must not fail the turn
            # The English text is the fallback the whole design leans on; the
            # adapter has already logged what went wrong.
            return text
        return rendered.text if rendered.text.strip() else text

    return localize


async def localize_answer(
    answer: ComposedAnswer, localize: LocalizeText, *, turn: UserTurn
) -> ComposedAnswer:
    """Every block's text through the localizer; the block types, their
    ``source_ids`` and the answer's ``sources`` stay as they are — language
    changes the words, never the structure or the provenance."""

    content = [
        block.model_copy(update={"text": await localize(block.text, turn=turn)})
        for block in answer.content
    ]
    return answer.model_copy(update={"content": tuple(content)})
