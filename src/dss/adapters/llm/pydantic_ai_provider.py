"""``LLMProvider`` implemented against Pydantic AI.

The only import of the framework outside ``orchestration/`` (ADR-0001 §4.3): the
port keeps ``core/`` framework-agnostic, and structured-output mode is a detail
that stays here.

Retries and the per-call timeout live in this adapter, per spec 0004 — the core
catches whatever escapes after they are exhausted and applies the policy's
``fail_mode``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
        stream_debounce_seconds: float | None = 0.1,
    ) -> None:
        self._model = model
        self._name = name
        self._retries = retries
        self._output_mode = output_mode
        self._model_settings = {"temperature": temperature, "timeout": timeout}
        # How long `stream_text` groups pieces before handing them on. Every
        # frame downstream repeats the whole response envelope, so one frame per
        # token is mostly envelope; grouping 100ms of them cuts that to a
        # fraction and costs the farmer at most 100ms on the first word. `None`
        # disables it — one piece out for every piece the model emits.
        self._stream_debounce_seconds = stream_debounce_seconds

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

    async def text(
        self,
        *,
        system_prompt: str,
        user_query: str,
    ) -> str:
        """The whole answer, in one call.

        Retries apply here and nowhere else in prose generation: nothing has
        reached the caller yet, so a second attempt replaces the first
        invisibly.
        """

        agent: Agent[None, str] = Agent(
            self._model,
            name=self._name,
            system_prompt=system_prompt,
            retries=self._retries,
        )
        result = await agent.run(user_query, model_settings=self._model_settings)
        log_external_response("llm", output_schema="text", body=result.output)
        return result.output

    async def stream_text(
        self,
        *,
        system_prompt: str,
        user_query: str,
    ) -> AsyncIterator[str]:
        """Yield the model's prose in pieces as it is written.

        ``delta=True`` yields each new piece rather than the text so far, so a
        consumer concatenates rather than replaces. Pieces land on whatever
        boundary the model emits, grouped by ``stream_debounce_seconds`` —
        mid-word and mid-number both happen, and nothing here tidies them:
        joining them back must give the answer exactly, and a "helpful" re-split
        is how that guarantee gets lost.

        ``retries`` is deliberately not passed. Pydantic AI retries output
        validation, and bare prose has none; what fails mid-stream is transport,
        and re-issuing that would re-generate a *different* answer after pieces
        of the first one have already been sent. The caller's rule — no retry
        once a piece is out — is enforced by not offering one here.
        """

        agent: Agent[None, str] = Agent(
            self._model,
            name=self._name,
            system_prompt=system_prompt,
        )
        written: list[str] = []
        async with agent.run_stream(
            user_query, model_settings=self._model_settings
        ) as stream:
            async for delta in stream.stream_text(
                delta=True, debounce_by=self._stream_debounce_seconds
            ):
                written.append(delta)
                yield delta
        # Logged once the stream drains, not per piece: a log line per token
        # would bury every other line in the turn.
        log_external_response("llm", output_schema="text", body="".join(written))


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
