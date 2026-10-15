"""The translation layer, between the edge and the core (#174).

The edge hands it the farmer's redacted turn and gets back the core's events in
the farmer's language, still tagged: the edge reveals kept values after this
layer, so a translator only ever sees tags. The core runs on the translated
turn and never knows it was translated.

With no route for the turn's language, the turn and every event pass straight
through, so an untranslated answer still streams piece by piece.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import TYPE_CHECKING

from dss.adapters.observability.tracing import set_current_span_content
from dss.core.shared.answer_stream import AnswerStream
from dss.core.shared.models import TurnFinished, UserTurn
from dss.core.translation.service import TurnTranslation
from dss.observability.stages import Stage
from dss.observability.trace_log import trace_component

if TYPE_CHECKING:
    from dss.orchestration.orchestrator import CoreEvent


async def translate_in(
    farmer_turn: UserTurn, translation: TurnTranslation | None, *, trace_id: str
) -> UserTurn:
    """The turn the core runs on: in the route's language, or the farmer's
    turn as it is when there is no route."""

    if translation is None:
        return farmer_turn
    with trace_component(Stage.TRANSLATION, trace_id):
        core_turn = await translation.translate_in(farmer_turn)
        set_current_span_content(
            input=farmer_turn.original_query, output=core_turn.original_query
        )
    return core_turn


async def translate_out(
    core_events: AsyncIterator[CoreEvent],
    translation: TurnTranslation | None,
    *,
    trace_id: str,
) -> AsyncGenerator[CoreEvent]:
    """The core's events in the farmer's language.

    Translation always streams, for every way out of the core: the composed
    answer and a fixed reply alike. The `TurnFinished` holds the whole answer,
    so the layer waits for it, drops the core's own pieces and `Claim`s, and
    streams each block's translation, then that block's `Claim`, then the
    `TurnFinished`. A translator that cannot stream yields each block once.
    """

    if translation is None:
        async for event in core_events:
            yield event
        return

    async for event in core_events:
        if not isinstance(event, TurnFinished):
            continue
        answer_stream = AnswerStream()
        translated: list[str] = []
        with trace_component(Stage.TRANSLATION, trace_id):
            for block in event.content:
                # `aclosing`: a farmer who closes the screen mid-answer must
                # close the translator's stream too.
                async with aclosing(
                    _stream_translation(block.text, translation)
                ) as pieces:
                    async for piece in pieces:
                        yield answer_stream.add_piece(piece)
                claim = answer_stream.claim(block, event.sources)
                translated.append(claim.content.text)
                yield claim
            set_current_span_content(
                input="\n".join(block.text for block in event.content),
                output="\n".join(translated),
            )
        yield answer_stream.finish(event.outcome)


def _stream_translation(text: str, translation: TurnTranslation) -> AsyncIterator[str]:
    """The translator's stream for `text`. A blank text is not sent: it goes
    out as it is."""

    if text.strip():
        return translation.stream_out(text)
    return _yield_unchanged(text)


async def _yield_unchanged(text: str) -> AsyncGenerator[str]:
    if text:
        yield text
