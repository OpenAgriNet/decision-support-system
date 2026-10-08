"""Tier 1 — the intent classifier over a mocked LLM.

Plain Python in/out; the ``LLMProvider`` and ``PromptProvider`` ports are
faked. No framework, no network, no files — the shipped template's own text is
pinned in ``tests/unit/config/test_shipped_prompts.py``; here we pin what the
service *tells* the template.
"""

from __future__ import annotations

from dss.core.intent.models import (
    ClassifiedAsk,
    IntentClassification,
    InteractionType,
    SubjectCategory,
)
from dss.core.intent.service import classify_intent
from dss.core.shared.models import ConversationMessage, UserTurn
from tests.support.fakes import FakePromptProvider


def _turn(
    query: str,
    history: list[ConversationMessage] | None = None,
    target_lang: str = "en",
) -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang=target_lang,
        channel="web",
        history=history or [],
    )


class _FakeLLM:
    """Records what it was asked and returns a scripted IntentClassification."""

    def __init__(self, result: IntentClassification) -> None:
        self._result = result
        self.seen_query: str | None = None
        self.seen_prompt: str | None = None

    async def structured(self, *, system_prompt, user_query, schema):
        self.seen_prompt = system_prompt
        self.seen_query = user_query
        assert schema is IntentClassification
        return self._result


async def test_classify_returns_asks_and_confidence() -> None:
    llm = _FakeLLM(
        IntentClassification(
            asks=(
                ClassifiedAsk(
                    agriculture_subjects="potato",
                    subject_categories=SubjectCategory.MARKET,
                    interaction_type=InteractionType.OBSERVE,
                ),
            ),
            confidence=0.88,
        )
    )

    classification = await classify_intent(
        _turn("What is the potato price?"), llm, FakePromptProvider()
    )

    assert classification == IntentClassification(
        asks=(
            ClassifiedAsk(
                agriculture_subjects="potato",
                subject_categories=SubjectCategory.MARKET,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=0.88,
    )
    assert llm.seen_query == "What is the potato price?"


async def test_history_reaches_the_prompt_for_followups() -> None:
    llm = _FakeLLM(IntentClassification())
    history = [
        ConversationMessage(role="user", text="What is the wheat price?"),
        ConversationMessage(role="assistant", text="Wheat is ₹2,275 per quintal."),
    ]

    await classify_intent(_turn("And potato?", history), llm, FakePromptProvider())

    assert "wheat price" in llm.seen_prompt.lower()
    # The raw follow-up is judged, not a rewrite.
    assert llm.seen_query == "And potato?"


async def test_only_the_recent_history_window_reaches_the_prompt() -> None:
    """Enough to resolve a reference, without ballooning the prompt."""

    llm = _FakeLLM(IntentClassification())
    history = [
        ConversationMessage(role="user", text=f"question {n}") for n in range(10)
    ]

    await classify_intent(_turn("And potato?", history), llm, FakePromptProvider())

    assert "question 9" in llm.seen_prompt
    assert "question 3" not in llm.seen_prompt


async def test_the_prompt_is_asked_for_in_the_turns_target_language() -> None:
    """The deployment decides per language which prompt serves (the port falls
    back to English on its own); this service's job is only to ask for the
    right one."""

    prompts = FakePromptProvider()

    await classify_intent(
        _turn("गेहूं का भाव?", target_lang="hi"), _FakeLLM(IntentClassification()), prompts
    )

    [(identifier, lang, kwargs)] = prompts.calls
    assert identifier == "INTENT"
    assert lang == "hi"


async def test_the_taxonomy_is_supplied_to_the_template() -> None:
    """The enums are the contract with the output schema, so the code — not
    each language's template — enumerates them. A category added to the enum
    reaches every language's prompt through these kwargs."""

    prompts = FakePromptProvider()

    await classify_intent(_turn("wheat?"), _FakeLLM(IntentClassification()), prompts)

    [(_, _, kwargs)] = prompts.calls
    for category in SubjectCategory:
        assert category.value in kwargs["categories"]
    for interaction in InteractionType:
        assert interaction.value in kwargs["interactions"]
