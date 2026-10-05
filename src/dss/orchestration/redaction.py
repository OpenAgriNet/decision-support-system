"""Run every configured identifier over a turn's texts, side by side, then redact.

Identifiers are independent — regex is instant, a model or an HTTP service takes
longer — so they run concurrently and the turn waits for the slowest. One that
fails is dropped for this turn and named in ``Redaction.failed``; the others'
spans still apply. Which identifiers exist, and how each works, is config and
adapters; this only composes them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import anyio

from dss.core.redaction.models import PiiSpan, RedactionPolicy
from dss.core.redaction.service import Redaction, redact
from dss.observability.trace_log import log_event
from dss.ports.pii_identifier import PiiIdentifier


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
        try:
            spans = await identifier.identify(texts)
            if len(spans) != len(texts):
                raise ValueError("returned a span list per text count that differs")
        except Exception as exc:  # noqa: BLE001 — any identifier failure is survivable
            # The type only: an exception message could quote the farmer's text.
            log_event(
                "redaction",
                request_id,
                event="identifier_failed",
                identifier=identifier.name,
                error=type(exc).__name__,
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
