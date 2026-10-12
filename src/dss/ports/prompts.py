"""The prompt port — where a component's system prompt comes from.

Every LLM component (intent, moderation, planner, composer, localizer) asks
this port for its system prompt instead of holding the text in code. What the
port promises:

- ``get_prompt(PromptIdentifier.INTENT, lang="hi", placeholder_values={...})``
  returns the Hindi version of the intent prompt when the deployment
  configured one, and the English version otherwise. **A missing language
  never fails a turn** — English is always there, and each prompt falls back
  on its own.
- ``placeholder_values`` carries what only the caller knows (the history
  window, the identity, the rendered sections); the template text itself is
  configuration.

The implementation (``config/prompt_service.py``) reads files and a YAML
registry, which is why this seam exists: ``core/`` may not read files or know
where prompts live (see ``tests/unit/test_core_isolation.py``), but it may
depend on this Protocol the same way it depends on ``LLMProvider``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from dss.core.planner.models import Skill


class PromptIdentifier(StrEnum):
    """The LLM components that ask for a prompt — the closed set of keys the
    registry (``configs/prompts.yaml``) must serve.

    An enum rather than bare strings so a typo'd identifier is an error where
    it is written, not a ``KeyError`` mid-turn; the values are the registry's
    component keys. Adding an LLM component means adding a member here and an
    entry (with at least ``en``) to the registry — the service checks the two
    stay complete at boot.
    """

    INTENT = "intent"
    MODERATION = "moderation"
    PLANNER = "planner"
    COMPOSER = "composer"
    LOCALIZER = "localizer"


@runtime_checkable
class PromptProvider(Protocol):
    def get_prompt(
        self,
        prompt_identifier: PromptIdentifier,
        lang: str,
        placeholder_values: Mapping[str, Any] | None = None,
    ) -> str:
        """The rendered system prompt for one component, in ``lang`` when the
        deployment configured a version for it, in English otherwise."""
        ...

    def get_skills(
        self, prompt_identifier: PromptIdentifier, lang: str
    ) -> tuple[Skill, ...]:
        """The skills configured beside that prompt, with the same language
        fallback. Empty for components that declare none."""
        ...
