"""Edge translation: a turn into the core's language, and its answer back.

The phone is never sent: after redaction it is only a tag.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

from dss.core.shared.errors import TranslationError
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import UserTurn
from dss.core.translation.models import TranslationRoute
from dss.ports.translator import Translator


@dataclass(frozen=True, slots=True)
class EdgeTranslation:
    """The configured languages. Built once at boot and handed to the
    orchestrator, which asks it for each turn's translation."""

    routes: Mapping[str, TranslationRoute]

    def for_turn(self, turn: UserTurn) -> TurnTranslation | None:
        """This turn's translation, or None when the DSS serves its language
        directly. Found by the language alone, so `gu-IN` is served by `gu`."""

        route = self.routes.get(turn.target_lang.primary)
        if route is None:
            return None
        return TurnTranslation(
            translator=route.translator,
            edge_lang=turn.target_lang,
            core_lang=route.to,
        )


@dataclass(frozen=True, slots=True)
class TurnTranslation:
    """One turn's translator, with the farmer's language and the core's fixed.
    A class, so no caller passes the pair or swaps it.
    """

    translator: Translator
    edge_lang: LanguageTag
    core_lang: LanguageTag

    async def translate_in(self, turn: UserTurn) -> UserTurn:
        """A new turn in the core language, in one translate call."""

        translated = await _translate_each(
            self.translator,
            _collect_texts_to_translate(turn),
            from_lang=self.edge_lang,
            to_lang=self.core_lang,
        )
        return _replace_turn_texts(turn, translated, lang=self.core_lang)

    def stream_out(self, text: str) -> AsyncIterator[str]:
        """`text` in the farmer's language, as the translator writes it."""

        return self.translator.stream(
            text, from_lang=self.core_lang, to_lang=self.edge_lang
        )


def _has_own_enriched_query(turn: UserTurn) -> bool:
    return turn.enriched_query != turn.original_query


def _collect_texts_to_translate(turn: UserTurn) -> list[str]:
    """The question, the enriched question when it differs, then the history."""

    enriched = [turn.enriched_query] if _has_own_enriched_query(turn) else []
    return [
        turn.original_query,
        *enriched,
        *(message.text for message in turn.history),
    ]


def _replace_turn_texts(
    turn: UserTurn, translated: Sequence[str], *, lang: LanguageTag
) -> UserTurn:
    """`turn` with `translated` in place, in `_collect_texts_to_translate` order."""

    query, *rest = translated
    if _has_own_enriched_query(turn):
        enriched, *history = rest
    else:
        enriched, history = query, rest
    return turn.model_copy(
        update={
            "original_query": query,
            "enriched_query": enriched,
            "history": [
                message.model_copy(update={"text": text})
                for message, text in zip(turn.history, history, strict=True)
            ],
            "source_lang": lang,
            "target_lang": lang,
        }
    )


async def _translate_each(
    translator: Translator,
    texts: Sequence[str],
    *,
    from_lang: LanguageTag,
    to_lang: LanguageTag,
) -> tuple[str, ...]:
    """One result per text, in order.

    Blank texts are kept as they are, not sent: there is nothing to translate,
    and a translator that drops one would shift every text after it.
    """

    to_send = [text for text in texts if text.strip()]
    translated = await translator.translate(
        to_send, from_lang=from_lang, to_lang=to_lang
    )
    if len(translated) != len(to_send):
        raise TranslationError(
            f"sent {len(to_send)} texts to translate, got {len(translated)} back"
        )
    returned = iter(translated)
    return tuple(next(returned) if text.strip() else text for text in texts)
