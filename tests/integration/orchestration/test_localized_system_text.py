"""Tier 3 — every system-authored reply leaves through the localizer.

The orchestrator has four places where it writes to the farmer with no
composer run: a moderation refusal, the no-place clarification, the
nobody-serves answer, and the ambiguous-place question appended to a composed
answer. Each must pass through ``Components.localize`` so a non-English farmer
reads it in their language (ADR-0018). Whether English skips the call and a
failure falls back is the *localizer's* behaviour, pinned in tier 1
(``tests/unit/core/channel/test_localize.py``); here the localizer is a
marking fake and the claim is only that every one of the four exits routes
through it with the turn in hand.
"""

from __future__ import annotations

from dss.core.intent.models import (
    ClassifiedAsk,
    IntentClassification,
    InteractionType,
    SubjectCategory,
)
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import ClaimDelta, Geometry, TurnStatus
from dss.ports.area_lookup import AreaMatch
from tests.integration.orchestration.test_orchestrator import (
    _ANSWERED_EVIDENCE,
    _PUNE_MATCH,
    DELETE_COMMAND,
    Result,
    _build,
    _collect,
    _evidence,
    _FakeCompose,
    _FakePlan,
    _one_ask,
    _served_discovery,
    _turn,
)
from tests.support.fakes import FakeAreaLookup, FakeLocalizer


def _hindi(turn):
    """The same turn, asked by a Hindi farmer."""

    return turn.model_copy(update={"source_lang": "hi", "target_lang": "hi"})


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
    assert all(lang == "hi" for _, lang in localize.calls)


async def test_the_no_place_clarification_is_localized() -> None:
    localize = FakeLocalizer(mark=True)
    orch, _ = _build(
        intent=_one_ask(),
        discovery=_served_discovery(),
        plan=_FakePlan(_ANSWERED_EVIDENCE),
        compose=_FakeCompose("unused"),
        localize=localize,
    )

    events = await _collect(orch, _hindi(_turn(location=None)))

    finished = events[-1]
    assert finished.outcome.status is TurnStatus.REQUIRES_INPUT
    assert all(block.text.startswith("[hi] ") for block in finished.content)


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


async def test_the_appended_which_place_question_is_localized() -> None:
    """The composed answer is already in the farmer's language (the composer
    is told to reply in target_lang); only the appended question is system
    text. Its numbered option lines are the localizer's to keep verbatim —
    the fake marks the whole string, so this asserts routing, not wording."""

    def weather_in(place_name: str) -> ClassifiedAsk:
        return ClassifiedAsk(
            subject_categories=SubjectCategory.WEATHER,
            interaction_type=InteractionType.OBSERVE,
            place_name=place_name,
        )

    def aurangabad(state: str) -> AreaMatch:
        return AreaMatch(
            name="Aurangabad",
            region="IN-XX",
            within=("India", state),
            geometry=Geometry(coordinates=[75.3, 19.9]),
        )

    localize = FakeLocalizer(mark=True)
    orch, _ = _build(
        intent=IntentClassification(
            asks=(weather_in("Pune"), weather_in("Aurangabad")), confidence=0.9
        ),
        discovery=_served_discovery(),
        plan=_FakePlan(
            _evidence(
                results=(Result(ask_index=0, source_id="1", data={"rain": "none"}),),
                served=(0,),
            )
        ),
        compose=_FakeCompose("No rain in Pune [1]."),
        area_lookup=FakeAreaLookup(
            {
                "pune": [_PUNE_MATCH],
                "aurangabad": [aurangabad("Maharashtra"), aurangabad("Bihar")],
            }
        ),
        localize=localize,
    )

    events = await _collect(
        orch, _hindi(_turn("weather in Pune and Aurangabad", location=None))
    )

    question = "Which Aurangabad?\n1. Aurangabad, Maharashtra\n2. Aurangabad, Bihar"
    deltas = [e.text for e in events if isinstance(e, ClaimDelta)]
    # the composed answer streams untouched; the appended question is localized
    assert deltas[-1] == f"\n\n[hi] {question}"
    assert localize.calls == [(question, "hi")]
    # the pieces still join back to the recorded answer
    finished = events[-1]
    assert "".join(deltas) == finished.content[0].text


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
