"""STUB(#83) — an LLM that answers from a table.

TODO(#83): delete this module once a real provider is wired for every
component. `adapters/llm/pydantic_ai_provider.py` already implements the port;
this is selected only when `Settings.stub_llm` is set, and the whole file goes
with that flag.

No network, no vendor SDK, no clock. It records what it was asked so a test can
assert *that* a stage ran, or that a stage was skipped.

It holds no business rules: it decides nothing about confidence, and a missing
schema is a wiring mistake rather than a runtime path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import LlmModerationVerdict

# The answers a stub deployment hands back. They live here, not in the
# composition root: the root names concrete *classes*, and reaching past
# `core.shared` for a model would let the transport package import a core
# slice — which the transport boundary check forbids.
DEFAULT_ANSWERS: dict[type[BaseModel], object] = {
    # Moderation's LLM policies fail *closed*, so a stub with no verdict here
    # turns every turn into `moderation_unavailable` — which is exactly what
    # happened before this entry existed.
    LlmModerationVerdict: LlmModerationVerdict(violated_policy_id=None),
    Intent: Intent(
        asks=(
            Ask(
                agriculture_subjects="wheat",
                subject_categories=SubjectCategory.MARKET,
                interaction_type=InteractionType.OBSERVE,
            ),
        ),
        confidence=0.9,
    ),
}


# What a stub deployment's composer writes. Several pieces, so a caller that
# only ever sees one has a stream that is not streaming.
DEFAULT_TEXT_CHUNKS: tuple[str, ...] = (
    "This is a stubbed answer. ",
    "No model was called.",
)


@dataclass(frozen=True)
class Ask:
    """One recorded request. ``schema`` is ``None`` for a text stream — prose is
    not a schema, which is why the port has two methods at all."""

    system_prompt: str
    user_query: str
    schema: type[BaseModel] | None


class StubLLM:
    def __init__(
        self,
        table: Mapping[type[BaseModel], Any] | None = None,
        *,
        text_chunks: Sequence[str] | None = None,
    ) -> None:
        self._table = dict(DEFAULT_ANSWERS if table is None else table)
        self._text_chunks = tuple(
            DEFAULT_TEXT_CHUNKS if text_chunks is None else text_chunks
        )
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

    async def stream_text(
        self,
        *,
        system_prompt: str,
        user_query: str,
    ) -> AsyncIterator[str]:
        """Hand back the wired pieces one at a time.

        No canned-answer table and no `KeyError`: an unwired *schema* is a
        wiring mistake because a caller asked for a shape nothing produces,
        where unwired text is just a turn that composed nothing.
        """

        self.calls.append(Ask(system_prompt, user_query, None))
        for chunk in self._text_chunks:
            yield chunk

    def asked_for(self, schema: type[BaseModel]) -> int:
        return sum(1 for call in self.calls if call.schema is schema)
