"""Concrete ``LLMProvider`` implementations — vendor SDKs live here, not in core.

One class per provider family; each implements ``dss.ports.LLMProvider`` and is
injected at the edge. Only these adapters (and ``orchestration``) may import a
vendor SDK.
"""

from dss.adapters.llm.openai_provider import DEFAULT_MODEL, OpenAILLMProvider

__all__ = ["DEFAULT_MODEL", "OpenAILLMProvider"]
