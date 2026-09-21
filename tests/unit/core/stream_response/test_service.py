"""Tier 1 — the stream response component.

`Evidence` in, the farmer's answer out in pieces. The LLM is a fake
`LLMProvider`, so these assert what the component does with what the model
gives it — the order it hands pieces on, what it puts in the prompt, and the
one rule the whole feature rests on: joining the pieces gives the answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from dss.core.planner.models import (
    Evidence,
    Failure,
    Identity,
    Result,
    Source,
    SourceKind,
)
from dss.core.shared.models import UserTurn
from dss.core.stream_response.service import stream_response

IDENTITY = Identity(
    name="Kisan Mitra",
    persona="A calm, practical farm advisor.",
    boundaries="Never gives financial advice.",
)

EVIDENCE = Evidence(
    sources=(Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None),),
    results=(
        Result(
            ask_index=0,
            source_id="1",
            data={
                "commodity": {"name": "Paddy"},
                "prices": {"modal": 2200, "unit": "INR/quintal"},
            },
        ),
    ),
    served=(0,),
    failed=(),
    sufficient=True,
)

NOTHING_FOUND = Evidence(sources=(), results=(), served=(), failed=(), sufficient=False)

UNREACHABLE = Evidence(
    sources=(),
    results=(),
    served=(),
    failed=(
        Failure(
            capability="openagrinet:MandiPrice",
            reason="429 too many requests",
            retryable=True,
        ),
    ),
    sufficient=False,
)

# Split mid-word and mid-number on purpose — that is what a model does.
CHUNKS = ("Paddy is ", "Rs 2,2", "00 per quin", "tal. [1]")
WHOLE = "Paddy is Rs 2,200 per quintal. [1]"


class _FakeLLM:
    """Streams fixed pieces and records the prompts it was given.

    `fail_after=n` raises once `n` pieces are out, which is how the no-retry
    rule is reached without a real model.
    """

    def __init__(
        self,
        chunks: Sequence[str] = CHUNKS,
        *,
        fail_after: int | None = None,
    ) -> None:
        self._chunks = tuple(chunks)
        self._fail_after = fail_after
        self.system_prompts: list[str] = []
        self.user_queries: list[str] = []

    async def structured(
        self, *, system_prompt, user_query, schema
    ):  # pragma: no cover
        raise AssertionError("the composer writes prose, not a schema")

    async def text(self, *, system_prompt: str, user_query: str) -> str:
        self.system_prompts.append(system_prompt)
        self.user_queries.append(user_query)
        return "".join(self._chunks)

    async def stream_text(
        self, *, system_prompt: str, user_query: str
    ) -> AsyncIterator[str]:
        self.system_prompts.append(system_prompt)
        self.user_queries.append(user_query)
        for index, chunk in enumerate(self._chunks):
            if index == self._fail_after:
                raise RuntimeError("the model stream dropped")
            yield chunk

    @property
    def everything(self) -> str:
        return "\n".join(self.system_prompts + self.user_queries)


def _turn(query: str = "What is the price of paddy?") -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        transaction_id="txn-1",
        session_id="s-1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


async def _collect(llm: _FakeLLM, evidence: Evidence = EVIDENCE) -> list[str]:
    return [
        delta
        async for delta in stream_response(
            evidence, turn=_turn(), identity=IDENTITY, llm=llm
        )
    ]


async def test_the_pieces_join_back_to_the_whole_answer() -> None:
    """The guarantee every other part of the feature leans on: what the farmer
    reads as it arrives is exactly what the finished answer says."""

    assert "".join(await _collect(_FakeLLM())) == WHOLE


async def test_pieces_are_handed_on_in_the_order_the_model_wrote_them() -> None:
    assert await _collect(_FakeLLM()) == list(CHUNKS)


async def test_nothing_is_reshaped_on_the_way_through() -> None:
    """A piece may end mid-word or mid-number. Tidying that up would break the
    join, so the component must pass pieces on exactly as they came."""

    assert await _collect(_FakeLLM()) == [
        "Paddy is ",
        "Rs 2,2",
        "00 per quin",
        "tal. [1]",
    ]


async def test_the_prompt_carries_the_question_and_the_providers_values() -> None:
    llm = _FakeLLM()

    await _collect(llm)

    assert "What is the price of paddy?" in llm.everything
    assert "2200" in llm.everything
    assert "Paddy" in llm.everything
    assert "Agmarknet" in llm.everything


async def test_the_identity_reaches_the_model() -> None:
    llm = _FakeLLM()

    await _collect(llm)

    assert "Kisan Mitra" in llm.everything
    assert "Never gives financial advice." in llm.everything


async def test_nothing_retrieved_says_so_rather_than_leaving_the_block_empty() -> None:
    """An empty block invites the model to fill it from its own knowledge."""

    llm = _FakeLLM()

    await _collect(llm, NOTHING_FOUND)

    assert "Nothing was retrieved." in llm.everything


async def test_a_failed_provider_is_named_rather_than_read_as_no_provider() -> None:
    """ "We could not reach Agmarknet" and "nobody serves this" are the same
    empty result and very different things to tell a farmer."""

    llm = _FakeLLM()

    await _collect(llm, UNREACHABLE)

    assert "openagrinet:MandiPrice" in llm.everything
    assert "429 too many requests" in llm.everything
    assert "Nothing was retrieved." not in llm.everything


async def test_a_model_that_writes_nothing_streams_nothing() -> None:
    """Not a failure. A turn that composed nothing is representable, and what
    it means is the caller's decision, not this component's."""

    assert await _collect(_FakeLLM(chunks=())) == []


async def test_a_failure_part_way_through_is_not_swallowed() -> None:
    """Once a piece is out there is no retry and no recovery here — the caller
    turns this into a failed turn, because pretending the answer finished would
    leave the farmer with half a sentence and no explanation."""

    llm = _FakeLLM(fail_after=2)
    seen: list[str] = []

    with pytest.raises(RuntimeError, match="stream dropped"):
        async for delta in stream_response(
            EVIDENCE, turn=_turn(), identity=IDENTITY, llm=llm
        ):
            seen.append(delta)

    assert seen == ["Paddy is ", "Rs 2,2"], "pieces already out stay out"
