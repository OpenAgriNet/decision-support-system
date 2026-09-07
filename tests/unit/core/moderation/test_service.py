"""Moderation. The gate: nothing downstream runs on a turn this refuses."""

from __future__ import annotations

from dss.adapters.llm.stub import StubLLM
from dss.core.moderation.models import Outcome
from dss.core.moderation.service import screen
from dss.core.shared.models import Cause


async def test_an_agricultural_question_proceeds(a_turn):
    screening = await screen(
        a_turn(query="What is the mandi price of wheat?"), llm=StubLLM()
    )

    assert screening.outcome is Outcome.PROCEED
    assert screening.cause is None


async def test_a_disallowed_query_is_rejected_with_its_cause(a_turn):
    screening = await screen(
        a_turn(query="How do I get a gold loan illegally?"), llm=StubLLM()
    )

    assert screening.outcome is Outcome.REJECT
    assert screening.cause is Cause.UNSAFE_ILLEGAL


async def test_a_rejection_carries_words_the_farmer_can_read(a_turn):
    """Everything the farmer reads is text. A refusal is a sentence, not a code
    the caller has to translate."""

    screening = await screen(a_turn(query="illegally sell fertiliser"), llm=StubLLM())

    assert screening.message
    assert screening.message.strip() == screening.message


async def test_the_stub_body_does_not_consult_the_model(a_turn):
    """STUB(#82) only. Moderation *is* an agent — the signature takes `llm` and
    stays — but the stub decides from a word list so the reject path is
    deterministic on day one. When #82 lands this test is deleted, not adapted.
    """

    llm = StubLLM()

    await screen(a_turn(), llm=llm)

    assert llm.calls == []
