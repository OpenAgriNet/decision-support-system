"""Run every configured identifier over a turn's texts, side by side, then redact.

Each identifier gets one text per call, and every call runs concurrently, so the
turn waits for the slowest. An identifier that fails on any text is dropped for
the whole turn and named in ``Redaction.failed``; the others' spans still apply.
Which identifiers exist, and how each works, is config and adapters; this only
composes them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

import anyio

from dss.core.redaction.models import ENTITY_PATTERN, PiiSpan, RedactionPolicy
from dss.core.redaction.service import Redaction, redact
from dss.core.redaction.visibility import NOTHING_HELD
from dss.observability.trace_log import log_event
from dss.ports.pii_identifier import PiiIdentifier

_ENTITY = re.compile(ENTITY_PATTERN)


class RedactTexts(Protocol):
    """What the orchestrator calls first on every turn. The composition root
    binds ``redact_texts`` to the configured identifiers and policy, or hands
    over ``pass_through`` when redaction is off."""

    async def __call__(
        self, texts: Sequence[str], *, request_id: str | None = None
    ) -> Redaction: ...


async def pass_through(
    texts: Sequence[str], *, request_id: str | None = None
) -> Redaction:
    """Redaction is off: the texts come back unchanged, and nothing is held."""

    return Redaction(texts=tuple(texts), visibility=NOTHING_HELD, found={})


async def redact_texts(
    texts: Sequence[str],
    identifiers: Sequence[PiiIdentifier],
    policy: RedactionPolicy,
    *,
    request_id: str | None = None,
) -> Redaction:
    texts = list(texts)
    found: dict[int, list[list[PiiSpan]]] = {}
    failed: list[str] = []

    async def run(index: int, identifier: PiiIdentifier) -> None:
        spans: list[list[PiiSpan]] = [[] for _ in texts]

        async def one(text_index: int, text: str) -> None:
            found_in_text = list(await identifier.identify(text))
            for span in found_in_text:
                _check(span, text)
            spans[text_index] = found_in_text

        try:
            async with anyio.create_task_group() as group:
                for text_index, text in enumerate(texts):
                    group.start_soon(one, text_index, text)
        except Exception as exc:  # noqa: BLE001 — any identifier failure is survivable
            # The type only: an exception message could quote the farmer's text.
            log_event(
                "redaction",
                request_id,
                event="identifier_failed",
                identifier=identifier.name,
                error=_first_error(exc),
            )
            failed.append(identifier.name)
            return
        found[index] = spans

    async with anyio.create_task_group() as group:
        for index, identifier in enumerate(identifiers):
            group.start_soon(run, index, identifier)

    # Merge in config order, so resolve's tie-break follows the rules file.
    merged: list[list[PiiSpan]] = [[] for _ in texts]
    for index in sorted(found):
        for text_index, spans in enumerate(found[index]):
            merged[text_index].extend(spans)

    result = redact(texts, merged, policy)
    return replace(result, failed=tuple(sorted(failed))) if failed else result


def _first_error(exc: BaseException) -> str:
    """The type of the first real error, past the task group's wrapper."""

    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__


def _check(span: PiiSpan, text: str) -> None:
    """A span that would corrupt the text, or could never be revealed, makes
    the whole identifier fail for this turn — its other spans are not trusted
    either."""

    if not 0 <= span.start < span.end <= len(text):
        raise ValueError("span offsets fall outside the text")
    if not _ENTITY.fullmatch(span.entity):
        raise ValueError("entity is not tag-safe")
    if not span.value:
        raise ValueError("span has no value")
