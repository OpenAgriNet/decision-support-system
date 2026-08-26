"""The LLM port — the seam between framework-agnostic core and the vendor SDK.

``core/`` depends on this Protocol; ``adapters/llm/`` implements it against
Pydantic AI (ADR-0001 §4.3). Structured-output mode
(``PromptedOutput``/``NativeOutput``) is a Pydantic AI detail that must stay
inside the adapter — this port only promises "given a system prompt and a user
query, return an instance of this schema".
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    """A per-function model binding. Retries and timeout live in the adapter; the
    core catches whatever this raises after those are exhausted and applies the
    policy's ``fail_mode``."""

    async def structured(
        self,
        *,
        system_prompt: str,
        user_query: str,
        schema: type[SchemaT],
    ) -> SchemaT: ...
