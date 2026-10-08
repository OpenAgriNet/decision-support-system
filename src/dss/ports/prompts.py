"""The prompt port — where a component's system prompt comes from.

Every LLM component (intent, moderation, planner, composer) asks this port for
its system prompt instead of holding the text in code. What the port promises:

- ``get_prompt("INTENT", lang="hi", kwargs={...})`` returns the Hindi version
  of the intent prompt when the deployment configured one, and the English
  version otherwise. **A missing language never fails a turn** — English is
  always there, and each prompt falls back on its own.
- ``kwargs`` carries what only the caller knows (the history window, the
  identity, the rendered sections); the template text itself is configuration.

The implementation (``config/prompt_service.py``) reads files and a YAML
registry, which is why this seam exists: ``core/`` may not read files or know
where prompts live (see ``tests/unit/test_core_isolation.py``), but it may
depend on this Protocol the same way it depends on ``LLMProvider``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from dss.core.planner.models import Skill


@runtime_checkable
class PromptProvider(Protocol):
    def get_prompt(
        self,
        prompt_identifier: str,
        lang: str,
        kwargs: Mapping[str, Any] | None = None,
    ) -> str:
        """The rendered system prompt for one component, in ``lang`` when the
        deployment configured a version for it, in English otherwise."""
        ...

    def get_skills(self, prompt_identifier: str, lang: str) -> tuple[Skill, ...]:
        """The skills configured beside that prompt, with the same language
        fallback. Empty for components that declare none."""
        ...
