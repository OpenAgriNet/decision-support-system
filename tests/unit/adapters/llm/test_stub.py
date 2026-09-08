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
