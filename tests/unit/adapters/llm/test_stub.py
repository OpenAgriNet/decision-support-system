"""The stub model. A missing answer is a wiring mistake, not a runtime path."""

from __future__ import annotations

import pytest

from dss.adapters.llm.stub import StubLLM
from dss.core.channel.models import ComposedAnswer
from dss.core.intent.models import Intent


async def test_an_unwired_schema_says_so_plainly():
    with pytest.raises(KeyError, match="did not expect to run"):
        await StubLLM({}).structured(
            system_prompt="p", user_query="q", schema=ComposedAnswer
        )


async def test_every_ask_is_recorded_for_inspection():
    llm = StubLLM()

    await llm.structured(system_prompt="p", user_query="q", schema=Intent)

    assert llm.asked_for(Intent) == 1
    assert llm.asked_for(ComposedAnswer) == 0
    assert llm.calls[0].system_prompt == "p"


async def test_streamed_text_arrives_in_pieces_and_is_recorded():
    """A stub deployment still has to *stream*, or nothing downstream of the
    composer can be exercised without a real model."""

    llm = StubLLM(text_chunks=("Paddy is ", "Rs 1,450."))

    deltas = [
        delta async for delta in llm.stream_text(system_prompt="p", user_query="q")
    ]

    assert deltas == ["Paddy is ", "Rs 1,450."]
    assert llm.calls[0].user_query == "q"
    assert llm.calls[0].schema is None


async def test_a_stub_with_no_text_wired_streams_nothing():
    """Not a failure — a turn that composes nothing is representable, and the
    caller decides what it means."""

    assert [
        delta
        async for delta in StubLLM(text_chunks=()).stream_text(
            system_prompt="p", user_query="q"
        )
    ] == []
