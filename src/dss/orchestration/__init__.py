"""Composition and control flow — the only package that may wire the
orchestration framework.

For this slice the control flow is deliberately thin: a turn fans out to intent
classification and moderation, which run independently and in parallel (ADR-0003).
The framework (Pydantic AI) is reached only through the injected ``LLMProvider``
adapters, so nothing here imports it directly yet.
"""
