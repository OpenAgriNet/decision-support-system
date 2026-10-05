"""Tier 3 — running the configured identifiers side by side, then redacting.

The identifiers here are fakes behind the port; what is under test is how they
are composed: concurrently, merged in config order, a failing one dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

import anyio

from dss.core.redaction.models import PiiSpan, RedactionPolicy, ValueHandling
from dss.orchestration.redaction import redact_texts

POLICY = RedactionPolicy(
    entities={"phone": ValueHandling.KEEP, "person": ValueHandling.KEEP}
)


class FindsWord:
    """Finds one fixed word wherever it appears, after an optional delay."""

    def __init__(self, name: str, word: str, entity: str, delay: float = 0) -> None:
        self.name = name
        self._word = word
        self._entity = entity
        self._delay = delay
        self.started: float | None = None

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        self.started = anyio.current_time()
        await anyio.sleep(self._delay)
        found = []
        for text in texts:
            start = text.find(self._word)
            found.append(
                []
                if start < 0
                else [
                    PiiSpan(
                        start,
                        start + len(self._word),
                        self._entity,
                        1.0,
                        self.name,
                        self._word,
                    )
                ]
            )
        return found


class Broken:
    name = "broken"

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        raise RuntimeError("service down")


class WrongShape:
    name = "wrong-shape"

    async def identify(self, texts: Sequence[str]) -> list[list[PiiSpan]]:
        return []


async def test_spans_from_every_identifier_are_applied() -> None:
    result = await redact_texts(
        ["Ramesh, 9876543210", "Ramesh ji"],
        [
            FindsWord("regex", "9876543210", "phone"),
            FindsWord("names", "Ramesh", "person"),
        ],
        POLICY,
    )
    assert result.texts == ("«person_1», «phone_1»", "«person_1» ji")
    assert result.failed == ()


async def test_identifiers_run_side_by_side() -> None:
    slow = FindsWord("slow", "x", "phone", delay=0.2)
    also_slow = FindsWord("also-slow", "y", "phone", delay=0.2)
    started = anyio.current_time()
    await redact_texts(["x y"], [slow, also_slow], POLICY)
    # Run one after the other this would take 0.4s.
    assert anyio.current_time() - started < 0.35


async def test_a_failing_identifier_is_dropped_and_named() -> None:
    result = await redact_texts(
        ["Ramesh, 9876543210"],
        [FindsWord("regex", "9876543210", "phone"), Broken()],
        POLICY,
    )
    assert result.texts == ("Ramesh, «phone_1»",)
    assert result.failed == ("broken",)


async def test_an_identifier_returning_the_wrong_shape_counts_as_failed() -> None:
    result = await redact_texts(["9876543210"], [WrongShape()], POLICY)
    assert result.texts == ("9876543210",)
    assert result.failed == ("wrong-shape",)


async def test_no_identifiers_leave_the_texts_unchanged() -> None:
    result = await redact_texts(["9876543210"], [], POLICY)
    assert result.texts == ("9876543210",)
    assert result.reveal.values == {}
