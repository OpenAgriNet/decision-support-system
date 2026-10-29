"""Tier 1 — rendering system-authored text in the farmer's language.

The ``LLMProvider`` and ``PromptProvider`` ports are faked. What this pins is
the mechanism's two promises (ADR-0018): English costs nothing, and a
localization failure never costs the answer.
"""

from __future__ import annotations

import anyio
import pytest

from dss.core.channel.localize import (
    LocalizedText,
    build_localizer,
    localize_answer,
)
from dss.core.channel.models import ComposedAnswer
from dss.core.forms.models import FieldErrorEntry, FormContext, FormErrorBlock
from dss.core.shared.language import LanguageTag
from dss.core.shared.models import (
    RefusalBlock,
    Source,
    SourceKind,
    TextBlock,
    TextQuery,
    UserTurn,
)
from dss.ports.prompts import PromptIdentifier
from tests.support.fakes import FakePromptProvider


def _turn(target_lang: str) -> UserTurn:
    return UserTurn(
        input=TextQuery(text="q"),
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
    assert "localizer" in system_prompt


async def test_the_localizer_prompt_is_asked_for_in_the_target_language() -> None:
    prompts = FakePromptProvider()
    localize = build_localizer(llm=_FakeLLM(), prompts=prompts)

    await localize("some text", turn=_turn("mr"))

    [(identifier, lang, kwargs)] = prompts.calls
    assert identifier is PromptIdentifier.LOCALIZER
    assert lang == LanguageTag("mr")
    assert kwargs == {"target_lang": LanguageTag("mr")}


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


async def test_a_form_error_block_has_its_messages_localized() -> None:
    """The farmer reads the field messages; the names and labels are the
    form's and stay as they are."""

    localize = build_localizer(llm=_FakeLLM(), prompts=FakePromptProvider())
    block = FormErrorBlock(
        form_id="offer-1",
        context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
        submission_id="submission-1",
        fields=(
            FieldErrorEntry(
                name="applicantMobile",
                label="Applicant mobile",
                error="Applicant mobile is not in the right format.",
            ),
        ),
    )

    localized = await localize_answer(
        ComposedAnswer(content=(block,)), localize, turn=_turn("hi")
    )

    (errors,) = localized.content
    assert isinstance(errors, FormErrorBlock)
    assert errors.fields == (
        FieldErrorEntry(
            name="applicantMobile",
            label="Applicant mobile",
            error="localized Applicant mobile is not in the right format.",
        ),
    )


class _CountsInFlight:
    """A `LocalizeText` that records how many calls overlap."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def __call__(self, text: str, *, turn: UserTurn) -> str:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await anyio.sleep(0.01)
        self.in_flight -= 1
        return f"localized {text}"


async def test_the_messages_of_one_answer_are_localized_together() -> None:
    """Five bad fields must not cost five model round trips in a row."""

    localize = _CountsInFlight()
    entries = tuple(
        FieldErrorEntry(name=f"field{n}", label=f"Field {n}", error=f"Message {n}")
        for n in range(3)
    )
    answer = ComposedAnswer(
        content=(
            TextBlock(text="Please check the fields marked below."),
            FormErrorBlock(
                form_id="offer-1",
                context=FormContext(
                    scheme="PMFBY", category="Grievance", action="support"
                ),
                submission_id="submission-1",
                fields=entries,
            ),
        )
    )

    localized = await localize_answer(answer, localize, turn=_turn("hi"))

    assert localize.peak == 4
    text, errors = localized.content
    assert isinstance(text, TextBlock)
    assert text.text == "localized Please check the fields marked below."
    assert isinstance(errors, FormErrorBlock)
    assert [entry.error for entry in errors.fields] == [
        "localized Message 0",
        "localized Message 1",
        "localized Message 2",
    ]
