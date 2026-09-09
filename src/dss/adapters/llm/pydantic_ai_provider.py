"""``LLMProvider`` implemented against Pydantic AI.

The only import of the framework outside ``orchestration/`` (ADR-0001 §4.3): the
port keeps ``core/`` framework-agnostic, and structured-output mode is a detail
that stays here.

Retries and the per-call timeout live in this adapter, per spec 0004 — the core
catches whatever escapes after they are exhausted and applies the policy's
``fail_mode``.
"""

from __future__ import annotations

from typing import Literal

from openai import AsyncOpenAI
from pydantic_ai import Agent, NativeOutput, PromptedOutput
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from dss.ports.llm import SchemaT

# How the schema is coaxed out of the model. ``tool`` (Pydantic AI's default) asks
# via a function-call — the strongest option, but it needs a model/endpoint that
# supports tools. ``native`` uses the provider's JSON-schema response format.
# ``prompted`` puts the schema in the prompt and parses JSON from plain text — the
# only mode that works against a model/endpoint that does not support tools.
OutputMode = Literal["tool", "native", "prompted"]


class PydanticAILLMProvider:
    """A per-function model binding. ``model`` is a configured Pydantic AI ``Model``
    object — built by one of the ``build_*`` factories below, which own the
    provider/endpoint wiring. Set ``output_mode="prompted"`` for a model that does
    not support tools."""

    def __init__(
        self,
        model: Model,
        *,
        temperature: float = 0.0,
        timeout: float = 5.0,
        retries: int = 1,
        output_mode: OutputMode = "tool",
    ) -> None:
        self._model = model
        self._retries = retries
        self._output_mode = output_mode
        self._model_settings = {"temperature": temperature, "timeout": timeout}

    def _output_type(self, schema: type[SchemaT]):
        if self._output_mode == "prompted":
            return PromptedOutput(schema)
        if self._output_mode == "native":
            return NativeOutput(schema)
        return schema  # "tool" — Pydantic AI's default for a bare schema

    async def structured(
        self,
        *,
        system_prompt: str,
        user_query: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        agent: Agent[None, SchemaT] = Agent(
            self._model,
            output_type=self._output_type(schema),
            system_prompt=system_prompt,
            retries=self._retries,
        )
        result = await agent.run(user_query, model_settings=self._model_settings)
        return result.output


def build_azure_llm(
    deployment: str,
    *,
    endpoint: str,
    api_key: str,
    temperature: float = 0.0,
    timeout: float = 60.0,
    retries: int = 2,
    output_mode: OutputMode = "tool",
) -> PydanticAILLMProvider:
    """An ``LLMProvider`` backed by an Azure OpenAI **v1** deployment via its
    Responses API. Keeps the SDK construction inside the adapter.

    ``endpoint`` is the v1 base or the full responses URL — e.g.
    ``https://<res>.services.ai.azure.com/openai/v1`` (a trailing ``/responses`` is
    trimmed). ``deployment`` is the Azure *deployment id* (no spaces), not a display
    name. The key is sent both as the ``api-key`` header (Azure key auth) and as a
    bearer token, so either auth style on the v1 endpoint works.

    ``output_mode`` defaults to ``"tool"`` — a capable hosted model does structured
    output best via function-calling; switch to ``"native"``/``"prompted"`` if a
    given deployment rejects tools.
    """

    return PydanticAILLMProvider(
        build_azure_model(deployment, endpoint=endpoint, api_key=api_key),
        temperature=temperature,
        timeout=timeout,
        retries=retries,
        output_mode=output_mode,
    )


def build_azure_model(
    deployment: str, *, endpoint: str, api_key: str
) -> OpenAIResponsesModel:
    """A model bound to an Azure OpenAI v1 deployment.

    Separate from `build_azure_llm` because the planner and composer are
    Pydantic AI agents that take a model directly, not an `LLMProvider` —
    so all four agents share this one construction.

    Azure needs three things a model string cannot express: calls go to a
    per-resource `endpoint`, the model name is a *deployment id*, and the key
    travels in an `api-key` header. It is also sent as a bearer token, so
    either auth style on the v1 endpoint works.
    """

    base_url = endpoint.rstrip("/")
    if base_url.endswith("/responses"):
        base_url = base_url[: -len("/responses")]

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        default_headers={"api-key": api_key},
    )
    return OpenAIResponsesModel(
        deployment, provider=OpenAIProvider(openai_client=client)
    )
