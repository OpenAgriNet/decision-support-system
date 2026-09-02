"""Tier-2 contract tests for the OpenAI-backed ``LLMProvider``.

A stubbed client stands in for the SDK (no live network — ``CLAUDE.md`` tier 2).
These assert the adapter's edge behaviour: it parses a JSON object out of a
completion, selects structured output vs JSON mode, and maps SDK failures onto
the port's ``LLMTimeoutError`` / ``LLMError`` — not core business logic.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import openai
import pytest

from dss.adapters.llm import DEFAULT_MODEL, OpenAILLMProvider
from dss.ports import LLMError, LLMTimeoutError


class StubCompletions:
    def __init__(self, *, content=None, raises=None):
        self._content = content
        self._raises = raises
        self.kwargs: dict | None = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _provider(*, content=None, raises=None):
    completions = StubCompletions(content=content, raises=raises)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = OpenAILLMProvider(client=client, model="gpt-5.6-luna")
    return provider, completions


def _run(provider, **kwargs):
    return asyncio.run(provider.generate_json(system="s", user="u", **kwargs))


def _timeout_error():
    return openai.APITimeoutError(request=SimpleNamespace(method="POST", url="x"))


def test_default_model_is_chatgpt_5_6_luna():
    assert DEFAULT_MODEL == "gpt-5.6-luna"


def test_parses_a_json_object_from_the_completion():
    provider, _ = _provider(content=json.dumps({"asks": [], "confidence": 0.5}))

    assert _run(provider) == {"asks": [], "confidence": 0.5}


def test_uses_structured_output_when_a_schema_is_given():
    provider, completions = _provider(content="{}")
    schema = {"type": "object"}

    _run(provider, schema=schema)

    fmt = completions.kwargs["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == schema
    assert completions.kwargs["model"] == "gpt-5.6-luna"


def test_falls_back_to_json_mode_without_a_schema():
    provider, completions = _provider(content="{}")

    _run(provider)

    assert completions.kwargs["response_format"] == {"type": "json_object"}


def test_timeout_maps_to_llm_timeout_error():
    provider, _ = _provider(raises=_timeout_error())

    with pytest.raises(LLMTimeoutError):
        _run(provider)


def test_other_sdk_errors_map_to_llm_error():
    provider, _ = _provider(raises=openai.OpenAIError("nope"))

    with pytest.raises(LLMError):
        _run(provider)


def test_non_json_content_is_an_llm_error():
    provider, _ = _provider(content="not json")

    with pytest.raises(LLMError):
        _run(provider)


def test_empty_completion_is_an_llm_error():
    provider, _ = _provider(content="")

    with pytest.raises(LLMError):
        _run(provider)


def test_json_that_is_not_an_object_is_an_llm_error():
    provider, _ = _provider(content="[1, 2, 3]")

    with pytest.raises(LLMError):
        _run(provider)
