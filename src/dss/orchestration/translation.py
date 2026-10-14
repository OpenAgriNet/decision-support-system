"""The translation layer, between the edge and the core (#174).

The edge hands it the farmer's redacted turn and gets back the core's events in
the farmer's language, still tagged: the edge reveals kept values after this
layer, so a translator only ever sees tags. The core runs on the translated
turn and never knows it was translated.

With no route for the turn's language, the turn and every event pass straight
through, so an untranslated answer still streams piece by piece.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import aclosing
from typing import TYPE_CHECKING

from dss.adapters.observability.tracing import set_current_span_content
from dss.core.shared.models import (
    Claim,
    ClaimDelta,
    OutputContent,
    Source,
    TurnFinished,
    UserTurn,
)
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
        claimed: list[OutputContent] = []
        with trace_component(Stage.TRANSLATION, trace_id):
            for block in event.content:
                # `aclosing`: a farmer who closes the screen mid-answer must
                # close the translator's stream too.
                async with aclosing(
                    _stream_block_out(block, event.sources, translation)
                ) as block_events:
                    async for block_event in block_events:
                        if isinstance(block_event, Claim):
                            claimed.append(block_event.content)
                        yield block_event
            set_current_span_content(
                input=_text_of(event.content), output=_text_of(claimed)
            )
        yield event.model_copy(update={"content": tuple(claimed)})


async def _stream_block_out(
    block: OutputContent,
    sources: tuple[Source, ...],
    translation: TurnTranslation,
) -> AsyncGenerator[ClaimDelta | Claim]:
    """One block in the farmer's language: its pieces as the translator writes
    them, then its `Claim`. A blank block is not sent."""

    if not block.text.strip():
        yield Claim(content=block, sources=sources)
        return
    pieces: list[str] = []
    async with aclosing(translation.stream_out(block.text)) as stream:
        async for piece in stream:
            pieces.append(piece)
            yield ClaimDelta(text=piece)
    translated = block.model_copy(update={"text": "".join(pieces)})
    yield Claim(content=translated, sources=sources)


def _text_of(content: Sequence[OutputContent]) -> str:
    return "\n".join(block.text for block in content)
