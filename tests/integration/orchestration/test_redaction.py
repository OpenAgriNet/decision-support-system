"""Tier 3 — running the configured identifiers side by side, then redacting.

The identifiers here are fakes behind the port; what is under test is how they
are composed: concurrently, merged in config order, a failing one dropped.
"""

from __future__ import annotations

import anyio
import pytest

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

    async def identify(self, text: str) -> list[PiiSpan]:
        self.started = anyio.current_time()
        await anyio.sleep(self._delay)
        start = text.find(self._word)
        if start < 0:
            return []
        return [
            PiiSpan(
                start, start + len(self._word), self._entity, 1.0, self.name, self._word
            )
        ]


class Broken:
    name = "broken"

    async def identify(self, text: str) -> list[PiiSpan]:
        raise RuntimeError("service down")


class WrongShape:
    name = "wrong-shape"

    async def identify(self, text: str) -> list[PiiSpan]:
        return None  # type: ignore[return-value]


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


async def test_each_text_is_identified_on_its_own() -> None:
    seen: list[str] = []

    class Records:
        name = "records"

        async def identify(self, text: str) -> list[PiiSpan]:
            seen.append(text)
            return []

    await redact_texts(["first", "second"], [Records()], POLICY)
    assert sorted(seen) == ["first", "second"]


async def test_a_failure_on_one_text_drops_the_identifier_for_the_turn() -> None:
    class FailsOnSecond:
        name = "flaky"

        async def identify(self, text: str) -> list[PiiSpan]:
            if text == "second 9876543210":
                raise RuntimeError("timeout")
            return [PiiSpan(6, 16, "phone", 1.0, self.name, "9876543210")]

    result = await redact_texts(
        ["first 9876543210", "second 9876543210"], [FailsOnSecond()], POLICY
    )
    # Its spans on the first text are not trusted either.
    assert result.texts == ("first 9876543210", "second 9876543210")
    assert result.failed == ("flaky",)


async def test_no_identifiers_leave_the_texts_unchanged() -> None:
    result = await redact_texts(["9876543210"], [], POLICY)
    assert result.texts == ("9876543210",)
    assert result.reveal.values == {}


class Returns:
    """Returns the given spans for the one text, whatever it is."""

    def __init__(self, name: str, *spans: PiiSpan) -> None:
        self.name = name
        self._spans = list(spans)

    async def identify(self, text: str) -> list[PiiSpan]:
        return list(self._spans)


@pytest.mark.parametrize(
    "bad",
    [
        PiiSpan(5, 99, "phone", 1.0, "x", "9876543210"),  # ends past the text
        PiiSpan(6, 3, "phone", 1.0, "x", "9876543210"),  # ends before it starts
        PiiSpan(-1, 4, "phone", 1.0, "x", "9876543210"),  # starts before the text
        PiiSpan(0, 4, "Phone", 1.0, "x", "9876543210"),  # not a tag-safe entity
        PiiSpan(0, 4, "phone", 1.0, "x", ""),  # no value to keep
    ],
)
async def test_an_identifier_returning_a_bad_span_counts_as_failed(bad) -> None:  # noqa: ANN001
    text = "call 9876543210"
    good = PiiSpan(5, 15, "phone", 1.0, "ok", "9876543210")

    result = await redact_texts(
        [text], [Returns("bad", bad), Returns("ok", good)], POLICY
    )

    # The bad identifier is dropped whole; the good one still applies.
    assert result.texts == ("call «phone_1»",)
    assert result.failed == ("bad",)
