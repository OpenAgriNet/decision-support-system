"""Tier 3 — every system-authored text leaves through the localizer.

The orchestrator writes to the farmer without the composer in three places:
a moderation refusal, the nobody-serves answer, and the pick list added after
a composed question. Each must pass through ``Components.localize`` so a
non-English farmer reads it in their language (ADR-0018). Whether English
skips the call and a failure falls back is the *localizer's* behaviour,
pinned in tier 1 (``tests/unit/core/channel/test_localize.py``). Here the
localizer is a marking fake, and the claim is only that each of the three
routes through it with the turn in hand.
"""

from __future__ import annotations

from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    NeedsClarification,
    Slot,
)
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import TurnStatus
from tests.integration.orchestration.test_orchestrator import (
    _ANSWERED_EVIDENCE,
    _PUNE_MATCH,
    DELETE_COMMAND,
    _build,
    _collect,
    _FakeCompose,
    _FakePlan,
    _one_ask,
    _served_discovery,
    _turn,
)
from tests.support.fakes import FakeLocalizer


def _hindi(turn):
    """The same turn, asked by a Hindi farmer."""

    return turn.model_copy(
        update={"source_lang": LanguageTag("hi"), "target_lang": LanguageTag("hi")}
    )


async def test_a_refusal_is_localized() -> None:
    localize = FakeLocalizer(mark=True)
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("unused"),
        violated="delete-command",
        policies=[DELETE_COMMAND],
        localize=localize,
    )

    events = await _collect(orch, _hindi(_turn("Delete the code")))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REJECTED
    assert all(block.text.startswith("[hi] ") for block in finished.content)
    assert localize.calls, "the refusal never reached the localizer"
    assert all(lang == LanguageTag("hi") for _, lang in localize.calls)


async def test_the_nobody_serves_answer_is_localized() -> None:
    localize = FakeLocalizer(mark=True)
    empty = DiscoveryResult(answers={}, capabilities={}, failures={}, events=())
    orch, _ = _build(
        intent=_one_ask(),
        discovery=empty,
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("unused"),
        localize=localize,
    )

    events = await _collect(orch, _hindi(_turn()))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.NO_MATCH
    assert all(block.text.startswith("[hi] ") for block in finished.content)


async def test_the_pick_list_is_localized() -> None:
    """The composer asks which one, in the farmer's language already. Only the
    list of choices is system text. The fake marks the whole list, so this
    asserts routing, not wording."""

    other_pune = _PUNE_MATCH.model_copy(update={"within": ("India", "Telangana")})
    which_pune = NeedsClarification(
        open_slots=(Slot(AskSlot.PLACE, Ambiguous((_PUNE_MATCH, other_pune))),)
    )
    localize = FakeLocalizer(mark=True)
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("पुणे कौन सा?"),
        localize=localize,
        settle_asks=lambda intent: (which_pune,),
    )

    events = await _collect(orch, _hindi(_turn()))

    choices = "1. Pune, Maharashtra\n2. Pune, Telangana"
    assert localize.calls == [(choices, LanguageTag("hi"))]
    question, pick_list = events[-1].content
    assert question.text == "पुणे कौन सा?"
    assert pick_list.text == f"[hi] {choices}"


async def test_an_answered_turn_sends_nothing_through_the_localizer() -> None:
    """The composed answer is the composer's own text, already in the farmer's
    language — routing it through the localizer would be a second model pass
    over prose that needs none."""

    localize = FakeLocalizer(mark=True)
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("गेहूं 2,275 रुपये प्रति क्विंटल है। [1]"),
        localize=localize,
    )

    events = await _collect(orch, _hindi(_turn()))

    assert events[-1].outcome.status is TurnStatus.ANSWERED
    assert localize.calls == []
