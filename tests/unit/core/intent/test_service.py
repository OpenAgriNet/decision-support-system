"""Intent recognition. A real business rule with a stub prompt behind it."""

from __future__ import annotations

from dss.adapters.llm.stub import StubLLM
from dss.core.intent.models import ActionType, Intent
from dss.core.intent.service import CONFIDENCE_FLOOR, recognise_intent


def _intent(confidence: float) -> Intent:
    return Intent(
        primary_domain="mandi-prices",
        action_type=ActionType.LOOKUP,
        confidence=confidence,
    )


async def test_a_confident_intent_is_returned(a_turn):
    expected = _intent(0.9)

    assert await recognise_intent(a_turn(), llm=StubLLM({Intent: expected})) is expected


async def test_an_intent_below_the_floor_is_not_returned(a_turn):
    """Below the floor the answer is "I don't know", not a guess. Returning a
    low-confidence intent would let routing act on a reading nobody trusts."""

    low = _intent(CONFIDENCE_FLOOR - 0.01)

    assert await recognise_intent(a_turn(), llm=StubLLM({Intent: low})) is None


async def test_the_floor_itself_is_confident_enough(a_turn):
    at_floor = _intent(CONFIDENCE_FLOOR)

    assert await recognise_intent(a_turn(), llm=StubLLM({Intent: at_floor})) is at_floor


async def test_the_query_is_what_the_model_is_asked_about(a_turn):
    llm = StubLLM({Intent: _intent(0.9)})

    await recognise_intent(a_turn(query="What is the price of potato?"), llm=llm)

    assert llm.calls[0].user_query == "What is the price of potato?"
