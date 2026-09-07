"""Response composition — how the answer is written for the channel."""

from __future__ import annotations

import pytest

from dss.adapters.llm.stub import StubLLM
from dss.core.channel.service import compose
from dss.core.intent.models import ActionType, Intent


@pytest.fixture
def intent():
    return Intent(
        primary_domain="mandi-prices", action_type=ActionType.LOOKUP, confidence=0.9
    )


async def test_an_answer_has_at_least_one_block(a_turn, intent):
    answer = await compose(a_turn(), intent, llm=StubLLM())

    assert answer.content


async def test_every_cited_source_id_is_one_the_answer_declares(a_turn, intent):
    """A block citing a source the answer does not list would render a footnote
    marker pointing at nothing."""

    answer = await compose(a_turn(), intent, llm=StubLLM())

    declared = {source.id for source in answer.sources}
    cited = {
        source_id
        for block in answer.content
        for source_id in getattr(block, "source_ids", ())
    }
    assert cited <= declared
