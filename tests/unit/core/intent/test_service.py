"""Tier 1 — the intent classifier over a mocked LLM.

Plain Python in/out; the ``LLMProvider`` port is faked. No framework, no network.
"""

from __future__ import annotations

from dss.core.intent.models import Capability, Intent, SubjectCategory
from dss.core.intent.service import build_intent_prompt, classify_intent
from dss.core.shared.models import ConversationMessage, UserTurn


def _turn(query: str, history: list[ConversationMessage] | None = None) -> UserTurn:
    return UserTurn(
        original_query=query,
        enriched_query=query,
        session_id="s1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=history or [],
    )


class _FakeLLM:
    """Records what it was asked and returns a scripted Intent."""

    def __init__(self, result: Intent) -> None:
        self._result = result
        self.seen_query: str | None = None
        self.seen_prompt: str | None = None

    async def structured(self, *, system_prompt, user_query, schema):
        self.seen_prompt = system_prompt
        self.seen_query = user_query
        assert schema is Intent
        return self._result


async def test_classify_returns_the_three_axes() -> None:
    expected = Intent(
        subject_categories=[SubjectCategory.MARKET],
        agriculture_subjects=["potato"],
        capabilities=[Capability.KNOWLEDGE],
    )
    llm = _FakeLLM(expected)

    intent = await classify_intent(_turn("What is the potato price?"), llm)

    assert intent == expected
    assert llm.seen_query == "What is the potato price?"


async def test_history_reaches_the_prompt_for_followups() -> None:
    llm = _FakeLLM(Intent())
    history = [
        ConversationMessage(role="user", text="What is the wheat price?"),
        ConversationMessage(role="assistant", text="Wheat is ₹2,275 per quintal."),
    ]

    await classify_intent(_turn("And potato?", history), llm)

    assert "wheat price" in llm.seen_prompt.lower()
    # The raw follow-up is judged, not a rewrite.
    assert llm.seen_query == "And potato?"


def test_prompt_lists_the_taxonomy_and_capabilities() -> None:
    prompt = build_intent_prompt([])
    for category in SubjectCategory:
        assert category.value in prompt
    for capability in Capability:
        assert capability.value in prompt


def test_prompt_without_history_has_no_conversation_section() -> None:
    assert "Conversation so far" not in build_intent_prompt([])
