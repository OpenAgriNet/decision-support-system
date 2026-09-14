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

from dss.observability.trace_log import log_external_response
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
        name: str,
        temperature: float = 0.0,
        timeout: float = 5.0,
        retries: int = 1,
        output_mode: OutputMode = "tool",
    ) -> None:
        self._model = model
        self._name = name
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
        # `name` is what Langfuse labels the span. Without it every agent in a
        # trace reads "agent run", and a turn's waterfall is four identical rows
        # you have to expand one by one to tell apart.
        agent: Agent[None, SchemaT] = Agent(
            self._model,
            name=self._name,
            output_type=self._output_type(schema),
            system_prompt=system_prompt,
            retries=self._retries,
        )
        result = await agent.run(user_query, model_settings=self._model_settings)
        log_external_response("llm", output_schema=schema.__name__, body=result.output)
        return result.output


def build_azure_model(
    deployment: str,
    *,
    endpoint: str,
    api_key: str,
) -> Model:
    """The bare Pydantic AI ``Model`` for an Azure OpenAI **v1** deployment via
    its Responses API.

    Split out from ``build_azure_llm`` because the planner and composer bind a
    ``Model`` directly (they build their own ``Agent``), while intent and
    moderation wrap it in a ``PydanticAILLMProvider``. Both need the same
    endpoint/auth wiring, so it lives here once.

    ``endpoint`` is the v1 base or the full responses URL — e.g.
    ``https://<res>.services.ai.azure.com/openai/v1`` (a trailing ``/responses`` is
    trimmed). ``deployment`` is the Azure *deployment id* (no spaces), not a display
    name. The key is sent both as the ``api-key`` header (Azure key auth) and as a
    bearer token, so either auth style on the v1 endpoint works. This bypasses
    pydantic-ai's ``AzureProvider`` (classic ``?api-version=`` API), which the v1
    GA endpoint rejects.
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


def build_azure_llm(
    deployment: str,
    *,
    name: str,
    endpoint: str,
    api_key: str,
    temperature: float = 0.0,
    timeout: float = 60.0,
    retries: int = 2,
    output_mode: OutputMode = "tool",
) -> PydanticAILLMProvider:
    """An ``LLMProvider`` backed by an Azure OpenAI **v1** deployment via its
    Responses API. Keeps the SDK construction inside the adapter.

    ``output_mode`` defaults to ``"tool"`` — a capable hosted model does structured
    output best via function-calling; switch to ``"native"``/``"prompted"`` if a
    given deployment rejects tools.
    """

    return PydanticAILLMProvider(
        build_azure_model(deployment, endpoint=endpoint, api_key=api_key),
        name=name,
        temperature=temperature,
        timeout=timeout,
        retries=retries,
        output_mode=output_mode,
    )
