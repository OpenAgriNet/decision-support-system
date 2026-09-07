"""Channel-response behaviour (design v2 §6.10).

PLACEHOLDER shaping, but the per-channel *shape* is real enough to exercise the
streaming contract: web/whatsapp stream a chunk per claim with inline ``[n]``
markers and a trailing source list; voice strips markers and drops the source
list; sms sends the whole message as one chunk with no markers. Real wording,
length budgeting (``response_max_chars``) and ``target_lang`` handling arrive with
the Channel Response component.

Only ``ANSWERED`` turns reach here as a full answer; ``shape`` is channel-agnostic
about *why* the turn ended — it shapes whatever ``Answer`` it is given.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dss.core.channel.models import ChannelChunk
from dss.core.composition.models import Answer
from dss.core.shared.models import UserTurn

_STREAMING_CHANNELS = {"web", "whatsapp"}


def _claim_line(text: str, source_id: str | None, *, cite: bool) -> str:
    if cite and source_id is not None:
        return f"{text} [{source_id}]"
    return text


def _source_list(answer: Answer) -> str:
    return "  ".join(f"[{s.id}] {s.name}" for s in answer.sources)


async def shape(answer: Answer, turn: UserTurn) -> AsyncIterator[ChannelChunk]:
    channel = turn.channel.lower()

    if channel == "sms":
        # One message, no streaming, no markers, no source list.
        text = " ".join(c.text for c in answer.claims)
        yield ChannelChunk(text=text, is_final=True)
        return

    cite = channel in _STREAMING_CHANNELS
    lines = [_claim_line(c.text, c.source_id, cite=cite) for c in answer.claims]
    source_list = _source_list(answer) if cite else ""

    trailing = [source_list] if source_list else []
    for index, line in enumerate(lines):
        last = index == len(lines) - 1 and not trailing
        yield ChannelChunk(text=line, is_final=last)
    if source_list:
        yield ChannelChunk(text=source_list, is_final=True)
