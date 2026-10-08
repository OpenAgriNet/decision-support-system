"""Tier 1 — rendering system-authored text in the farmer's language.

The ``LLMProvider`` and ``PromptProvider`` ports are faked. What this pins is
the mechanism's two promises (ADR-0018): English costs nothing, and a
localization failure never costs the answer.
"""

from __future__ import annotations

import pytest

from dss.core.channel.localize import (
    LocalizedText,
    build_localizer,
    localize_answer,
)
from dss.core.channel.models import ComposedAnswer
from dss.core.shared.models import RefusalBlock, Source, SourceKind, TextBlock, UserTurn
from tests.support.fakes import FakePromptProvider


def _turn(target_lang: str) -> UserTurn:
    return UserTurn(
        original_query="q",
        enriched_query="q",
        session_id="s1",
        transaction_id="t1",
        source_lang=target_lang,
        target_lang=target_lang,
        channel="web",
    )


class _FakeLLM:
    """Returns a marked rendering, or raises."""

    def __init__(self, *, boom: bool = False, answer: str | None = None) -> None:
        self._boom = boom
        self._answer = answer
        self.calls: list[tuple[str, str]] = []  # (system_prompt, user_query)

    async def structured(self, *, system_prompt, user_query, schema):
        self.calls.append((system_prompt, user_query))
        if self._boom:
            raise RuntimeError("model unavailable")
        assert schema is LocalizedText
        return LocalizedText(
            text=self._answer if self._answer is not None else f"localized {user_query}"
        )


async def test_a_non_english_turn_is_rendered_by_the_model() -> None:
    llm = _FakeLLM()
    localize = build_localizer(llm=llm, prompts=FakePromptProvider())

    rendered = await localize("Which place are you asking about?", turn=_turn("hi"))

    assert rendered == "localized Which place are you asking about?"
    # the message travels in the user slot; the system prompt is the localizer's
    [(system_prompt, user_query)] = llm.calls
    assert user_query == "Which place are you asking about?"
    assert "LOCALIZER" in system_prompt


async def test_the_localizer_prompt_is_asked_for_in_the_target_language() -> None:
    prompts = FakePromptProvider()
    localize = build_localizer(llm=_FakeLLM(), prompts=prompts)

    await localize("some text", turn=_turn("mr"))

    [(identifier, lang, kwargs)] = prompts.calls
    assert identifier == "LOCALIZER"
    assert lang == "mr"
    assert kwargs == {"target_lang": "mr"}


@pytest.mark.parametrize("tag", ["en", "en-IN", "EN"])
async def test_english_targets_cost_no_model_call(tag: str) -> None:
    """These paths were deliberately model-free; for English they stay so."""

    llm = _FakeLLM()
    localize = build_localizer(llm=llm, prompts=FakePromptProvider())

    rendered = await localize("I can't help with that request.", turn=_turn(tag))

    assert rendered == "I can't help with that request."
    assert llm.calls == []


async def test_a_model_failure_serves_the_english_text() -> None:
    """Worse than the farmer's language, far better than no reply."""

    localize = build_localizer(llm=_FakeLLM(boom=True), prompts=FakePromptProvider())

    rendered = await localize("Which place are you asking about?", turn=_turn("hi"))

    assert rendered == "Which place are you asking about?"


async def test_a_model_returning_nothing_serves_the_english_text() -> None:
    """An empty rendering is a failure in disguise — a blank refusal reads as
    the DSS going silent."""

    localize = build_localizer(llm=_FakeLLM(answer="  "), prompts=FakePromptProvider())

    rendered = await localize("I can't help with that request.", turn=_turn("hi"))

    assert rendered == "I can't help with that request."


async def test_empty_text_is_left_alone_without_a_call() -> None:
    llm = _FakeLLM()
    localize = build_localizer(llm=llm, prompts=FakePromptProvider())

    assert await localize("", turn=_turn("hi")) == ""
    assert llm.calls == []


async def test_localize_answer_keeps_structure_and_provenance() -> None:
    """Language changes the words, never the block types, the citations, or
    the sources."""

    localize = build_localizer(llm=_FakeLLM(), prompts=FakePromptProvider())
    source = Source(id="1", name="Agmarknet", kind=SourceKind.PROVIDER, url=None)
    answer = ComposedAnswer(
        content=(
            RefusalBlock(text="I can't help with that request."),
            TextBlock(text="Which place are you asking about?", source_ids=("1",)),
        ),
        sources=(source,),
    )

    localized = await localize_answer(answer, localize, turn=_turn("hi"))

    refusal, text = localized.content
    assert isinstance(refusal, RefusalBlock)
    assert refusal.text == "localized I can't help with that request."
    assert isinstance(text, TextBlock)
    assert text.text == "localized Which place are you asking about?"
    assert text.source_ids == ("1",)
    assert localized.sources == (source,)
