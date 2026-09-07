"""``LLMProvider`` implemented against Pydantic AI.

The only import of the framework outside ``orchestration/`` (ADR-0001 §4.3): the
port keeps ``core/`` framework-agnostic, and structured-output mode is a detail
that stays here.

Retries and the per-call timeout live in this adapter, per spec 0004 — the core
catches whatever escapes after they are exhausted and applies the policy's
``fail_mode``.
"""

from __future__ import annotations

from pydantic_ai import Agent

from dss.ports.llm import SchemaT


class PydanticAILLMProvider:
    """A per-function model binding for moderation. ``model`` is any Pydantic AI
    model id (``"openai:gpt-4o-mini"``) or model object; for a local
    OpenAI-compatible endpoint (e.g. a self-hosted Qwen) pass a configured
    ``OpenAIModel`` with its base URL."""

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        timeout: float = 5.0,
        retries: int = 1,
    ) -> None:
        self._model = model
        self._retries = retries
        self._model_settings = {"temperature": temperature, "timeout": timeout}

    async def structured(
        self,
        *,
        system_prompt: str,
        user_query: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        agent: Agent[None, SchemaT] = Agent(
            self._model,
            output_type=schema,
            system_prompt=system_prompt,
            retries=self._retries,
        )
        result = await agent.run(user_query, model_settings=self._model_settings)
        return result.output
