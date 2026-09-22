"""The LLM port — the seam between framework-agnostic core and the vendor SDK.

``core/`` depends on this Protocol; ``adapters/llm/`` implements it against
Pydantic AI (ADR-0001 §4.3). Structured-output mode
(``PromptedOutput``/``NativeOutput``) is a Pydantic AI detail that must stay
inside the adapter — this port only promises "given a system prompt and a user
query, return an instance of this schema", or — for prose, which is not a
schema — yield the answer in pieces as the model writes it.

``stream_text`` has no whole-answer twin. A caller who wants the answer in one
piece joins the pieces — and the absence is deliberate: a whole-answer call
could be retried, a streamed one cannot (a second attempt would write a
different answer after the first one's words are already out), and offering both
invites the two to drift into different prompts.

``stream_text`` is declared ``def``, not ``async def``: an implementation is an
async generator, and calling one returns the iterator without awaiting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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

    def stream_text(
        self,
        *,
        system_prompt: str,
        user_query: str,
    ) -> AsyncIterator[str]: ...
