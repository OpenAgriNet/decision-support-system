"""Tier 1 — the whole-answer composer.

`Evidence` in, the farmer's answer out in one piece. The LLM is a fake
`LLMProvider`, so these assert what reaches the *prompt* — the provider's
values and the farmer's question — not the canned answer, which would pass
even if the evidence were never put in the prompt at all.

The last test here is the one that matters most: this composer and the
streaming one are separate calls, and this is what stops them becoming
separate prompts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from dss.core.channel.compose import build_compose
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

ANSWER = "The price of paddy is 2,200 Rs."


class _Recorder:
    """Captures every prompt the model was sent, and answers with a fixed
    string so the assertions can be about the input."""

    def __init__(self, answer: str = ANSWER) -> None:
        self._answer = answer
        self.asked: list[tuple[str, str]] = []

    async def structured(
        self, *, system_prompt, user_query, schema
    ):  # pragma: no cover
        raise AssertionError("the composer writes prose, not a schema")

    async def text(self, *, system_prompt: str, user_query: str) -> str:
        self.asked.append((system_prompt, user_query))
        return self._answer

    async def stream_text(
        self, *, system_prompt: str, user_query: str
    ) -> AsyncIterator[str]:
        self.asked.append((system_prompt, user_query))
        yield self._answer

    @property
    def everything(self) -> str:
        return "\n".join(part for pair in self.asked for part in pair)


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


async def test_the_prompt_carries_the_question_and_the_providers_values() -> None:
    """Both are required to write "The price of paddy is 2,200 Rs.": the
    values come from the provider, and the question decides which of them the
    answer leads with."""

    recorder = _Recorder()
    compose = build_compose(identity=IDENTITY, llm=recorder)

    await compose(EVIDENCE, turn=_turn("What is the price of paddy?"))

    assert "What is the price of paddy?" in recorder.everything
    assert "2200" in recorder.everything
    assert "Paddy" in recorder.everything
    assert "Agmarknet" in recorder.everything


async def test_compose_returns_the_composed_answer() -> None:
    compose = build_compose(identity=IDENTITY, llm=_Recorder(ANSWER))

    assert await compose(EVIDENCE, turn=_turn()) == ANSWER


async def test_nothing_retrieved_says_so_in_the_prompt() -> None:
    """No results means the model must be told there is nothing, not handed
    an empty block it might fill from its own knowledge."""

    recorder = _Recorder()
    compose = build_compose(identity=IDENTITY, llm=recorder)

    await compose(NOTHING_FOUND, turn=_turn())

    assert "Nothing was retrieved." in recorder.everything
    # collapse the prompt's line wrapping so a phrase split across two lines
    # still matches
    assert "could not find it" in " ".join(recorder.everything.split())


async def test_a_failed_provider_is_named_rather_than_reading_as_no_provider() -> None:
    """ "We could not reach Agmarknet" and "nobody serves this" are the same
    empty result but very different things to tell a farmer. `Evidence.failed`
    exists for exactly that distinction — and the composer was not reading it,
    so both rendered as "Nothing was retrieved."."""

    recorder = _Recorder()
    compose = build_compose(identity=IDENTITY, llm=recorder)

    await compose(UNREACHABLE, turn=_turn())

    assert "openagrinet:MandiPrice" in recorder.everything
    assert "429 too many requests" in recorder.everything
    # and it must not read as "nobody serves this"
    assert "Nothing was retrieved." not in recorder.everything


async def test_both_composers_ask_the_model_exactly_the_same_thing() -> None:
    """The guard against the two paths drifting.

    They are separate calls — one retryable, one not — over one prompt. If this
    fails, a farmer's answer now depends on which endpoint they came in
    through, which is the bug this whole arrangement exists to prevent.
    """

    whole, streamed = _Recorder(), _Recorder()
    turn = _turn("What is the price of paddy?")

    await build_compose(identity=IDENTITY, llm=whole)(EVIDENCE, turn=turn)
    async for _ in stream_response(
        EVIDENCE, turn=turn, identity=IDENTITY, llm=streamed
    ):
        pass

    assert whole.asked == streamed.asked
