"""Tier 3 — the throwaway composer: Evidence in, an answer out.

A second LLM call, separate from the planner's loop, so ``Evidence`` stays
the seam the real Response Composer will read. Lives in ``orchestration/``
for now because it calls Pydantic AI directly — ``LLMProvider`` only offers
``structured()``, and prose is not a schema. Moving it to ``core/`` behind a
``text()`` port is the follow-up.

The model is a ``FunctionModel`` returning a canned string, so these tests
assert on what reaches the *prompt* — the provider's values and the farmer's
question — not on the canned output, which would pass even if the evidence
were never put in the prompt at all.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dss.core.planner.models import (
    Evidence,
    Failure,
    Identity,
    Result,
    Source,
    SourceKind,
)
from dss.core.shared.models import UserTurn
from dss.orchestration.compose import build_compose

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


class _Recorder:
    """Captures every prompt the model was sent, and answers with a fixed
    string so the assertions can be about the input."""

    def __init__(self, answer: str = "The price of paddy is 2,200 Rs.") -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def as_model(self) -> FunctionModel:
        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.prompts.extend(
                part.content
                for message in messages
                for part in message.parts
                if isinstance(part, SystemPromptPart | UserPromptPart)
                and isinstance(part.content, str)
            )
            return ModelResponse(parts=[TextPart(content=self._answer)])

        return FunctionModel(model)

    @property
    def everything(self) -> str:
        return "\n".join(self.prompts)


async def test_the_prompt_carries_the_question_and_the_providers_values() -> None:
    """Both are required to write "The price of paddy is 2,200 Rs.": the
    values come from the provider, and the question decides which of them the
    answer leads with."""

    recorder = _Recorder()
    compose = build_compose(identity=IDENTITY, model=recorder.as_model())

    await compose(EVIDENCE, turn=_turn("What is the price of paddy?"))

    assert "What is the price of paddy?" in recorder.everything
    assert "2200" in recorder.everything
    assert "Paddy" in recorder.everything
    assert "Agmarknet" in recorder.everything


async def test_compose_returns_the_composed_answer() -> None:
    recorder = _Recorder("The price of paddy is 2,200 Rs.")
    compose = build_compose(identity=IDENTITY, model=recorder.as_model())

    answer = await compose(EVIDENCE, turn=_turn())

    assert answer == "The price of paddy is 2,200 Rs."


async def test_nothing_retrieved_says_so_in_the_prompt() -> None:
    """No results means the model must be told there is nothing, not handed
    an empty block it might fill from its own knowledge."""

    recorder = _Recorder()
    compose = build_compose(identity=IDENTITY, model=recorder.as_model())

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
    compose = build_compose(identity=IDENTITY, model=recorder.as_model())

    await compose(UNREACHABLE, turn=_turn())

    assert "openagrinet:MandiPrice" in recorder.everything
    assert "429 too many requests" in recorder.everything
    # and it must not read as "nobody serves this"
    assert "Nothing was retrieved." not in recorder.everything
