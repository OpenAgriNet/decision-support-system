"""The LLM port — what the core needs from a language model.

Deliberately not what any SDK offers. There is no `temperature`, no
`model="..."`, no `messages=[...]`, no retry budget and no API key: those are a
vendor's vocabulary and belong in the adapter's constructor. A port that carried
them would have inverted nothing — every non-matching adapter would have to
translate backwards.

Retries and timeouts live in the adapter. Once they are exhausted the adapter
raises a domain error, and the caller in `core/` decides what that means.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@runtime_checkable
class LLM(Protocol):
    """One prompt, one query, one typed answer."""

    async def structured(
        self,
        *,
        system_prompt: str,
        user_query: str,
        schema: type[SchemaT],
    ) -> SchemaT: ...
