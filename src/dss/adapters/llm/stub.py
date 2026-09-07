"""STUB(#83) — an LLM that answers from a table.

No network, no vendor SDK, no clock. It records what it was asked so a test can
assert *that* a stage ran, or that a stage was skipped.

It holds no business rules: it decides nothing about confidence, and a missing
schema is a wiring mistake rather than a runtime path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory

# The answers a stub deployment hands back. They live here, not in the
# composition root: the root names concrete *classes*, and reaching past
# `core.shared` for a model would let the transport package import a core
# slice — which the transport boundary check forbids.
DEFAULT_ANSWERS: dict[type[BaseModel], object] = {
    Intent: Intent(
        asks=(
            Ask(
                agriculture_subjects="wheat",
                subject_categories=SubjectCategory.MARKET,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=0.9,
    )
}


@dataclass(frozen=True)
class Ask:
    """One recorded request."""

    system_prompt: str
    user_query: str
    schema: type[BaseModel]


class StubLLM:
    def __init__(self, table: Mapping[type[BaseModel], Any] | None = None) -> None:
        self._table = dict(DEFAULT_ANSWERS if table is None else table)
        self.calls: list[Ask] = []

    async def structured(
        self,
        *,
        system_prompt: str,
        user_query: str,
        schema: type[BaseModel],
    ) -> Any:
        self.calls.append(Ask(system_prompt, user_query, schema))
        if schema not in self._table:
            raise KeyError(
                f"StubLLM has no canned answer for {schema.__name__} — the test "
                "wired a stage it did not expect to run"
            )
        return self._table[schema]

    def asked_for(self, schema: type[BaseModel]) -> int:
        return sum(1 for call in self.calls if call.schema is schema)
