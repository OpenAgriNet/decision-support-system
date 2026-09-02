"""The LLM port — the seam between framework-agnostic core and a model provider.

``core`` never speaks to a vendor SDK directly. It depends on this Protocol; a
concrete client (OpenAI, Azure, vLLM, Anthropic) lives in ``dss.adapters.llm``
and is injected at the edge. That is what keeps intent recognition and the other
core services unit-testable with a fake and provider-neutral per the DPG's
interoperability principle (``docs/DSS_ARCHITECTURE.md`` ADR-0001 driver 2).

Timeouts are a first-class part of the contract: a timed-out call **raises**
``LLMTimeoutError``. Core is required never to fabricate a result on timeout
(``DSS_ARCHITECTURE.md`` §7; the intent spec's "a timeout must raise"), so the
error is surfaced as a distinct type the orchestration layer can map to the
contract's ``error`` outcome rather than being folded into a generic failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


class LLMError(Exception):
    """A model call failed in a way the caller cannot recover from in core.

    Malformed (non-JSON) output, an empty completion, or a transport error the
    adapter could not classify more precisely all surface as this.
    """


class LLMTimeoutError(LLMError):
    """The model call exceeded its deadline.

    Kept distinct from ``LLMError`` because the failure behaviour differs: a
    timeout must propagate so the turn ends in a controlled ``error`` outcome,
    never a made-up result (``DSS_ARCHITECTURE.md`` §7).
    """


@runtime_checkable
class LLMProvider(Protocol):
    """Generates a JSON object from a system + user prompt.

    Deliberately narrow and vendor-neutral: one method, plain-Python in and out.
    The adapter owns model selection, retries, JSON-mode/structured-output
    wiring, and translating vendor timeouts into ``LLMTimeoutError``. Core owns
    prompt construction and mapping the returned object onto domain types — so
    the rules that decide *what a valid intent is* stay in core, where tier-1
    tests can exercise them without a model.
    """

    async def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Return the model's response parsed as a JSON object.

        ``schema`` is an optional JSON Schema describing the expected shape; an
        adapter that supports structured output should constrain the model to
        it, and one that does not may ignore it and fall back to JSON mode. The
        return value is always a parsed object — never raw text.

        Raises:
            LLMTimeoutError: the call exceeded its deadline.
            LLMError: the call failed or returned something that is not a JSON
                object.
        """
        ...
