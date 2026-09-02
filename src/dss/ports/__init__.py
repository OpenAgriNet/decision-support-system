"""Interfaces the core depends on — Protocols and ABCs, no implementations.

This is the seam that lets core services be tested with fakes and adapters be
swapped without touching ``dss.core``.
"""

from dss.ports.llm import LLMError, LLMProvider, LLMTimeoutError

__all__ = ["LLMError", "LLMProvider", "LLMTimeoutError"]
