"""An ``LLMProvider`` backed by the OpenAI SDK.

This is an adapter: it implements the ``dss.ports.LLMProvider`` seam so core
never imports a vendor SDK. It talks to the OpenAI client **directly**, not
through Pydantic AI — the orchestration framework is confined to
``dss.orchestration`` (``CLAUDE.md`` folder rules, ADR-0001 §4.3). The default
model is "ChatGPT 5.6 Luna" (``gpt-5.6-luna``); it is overridable per tenant via
``LLM_MODEL`` so provider neutrality holds (ADR-0001 driver 2).

Responsibilities that live here, not in core:
- structured output: when a JSON Schema is supplied, constrain the model to it;
- timeout translation: an SDK timeout becomes ``LLMTimeoutError`` so the turn
  ends in a controlled error, never a fabricated intent (§7);
- shape enforcement: a non-JSON or non-object completion becomes ``LLMError``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from openai import APITimeoutError, AsyncOpenAI, OpenAIError

from dss.ports import LLMError, LLMTimeoutError

# "ChatGPT 5.6 Luna" — the model the intent slice defaults to. Override per
# tenant with LLM_MODEL to keep the DSS provider-neutral.
DEFAULT_MODEL = "gpt-5.6-luna"


class OpenAILLMProvider:
    """``LLMProvider`` over ``AsyncOpenAI``.

    Args:
        client: an ``AsyncOpenAI`` (or API-compatible) client. Injected so
            tier-2 tests can pass a stub and so an OpenAI-compatible gateway
            (Azure, vLLM's OpenAI shim) drops in unchanged. Constructed from the
            environment when omitted.
        model: model id; defaults to ``LLM_MODEL`` then ``DEFAULT_MODEL``.
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client or AsyncOpenAI()
        self._model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)

    async def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        try:
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=_response_format(schema),
                temperature=0,
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(f"model {self._model} timed out") from exc
        except OpenAIError as exc:
            raise LLMError(f"model {self._model} call failed") from exc

        return _parse_object(_content(completion))


def _response_format(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Prefer strict structured output; fall back to plain JSON mode."""
    if schema is None:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": "intent", "schema": dict(schema), "strict": True},
    }


def _content(completion: Any) -> str:
    try:
        content = completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMError("model returned no completion") from exc
    if not content:
        raise LLMError("model returned an empty completion")
    return content


def _parse_object(content: str) -> Mapping[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError("model returned non-JSON content") from exc
    if not isinstance(data, Mapping):
        raise LLMError("model returned JSON that is not an object")
    return data
