"""The prompt service — the ``ports.prompts.PromptProvider`` implementation.

``configs/prompts.yaml`` names, per LLM component and per language, which
template under ``prompts/`` is the system prompt (and, for the planner, which
skill files ride with it). This module loads that registry once at boot and
answers ``get_prompt("INTENT", lang="hi", kwargs={...})`` per turn.

The two properties the design leans on:

- **A missing override never fails a turn.** ``en`` is required for every
  component and is always loadable, so a turn in a language with no entry runs
  on the English prompt. The gap is recorded as a trace event
  (``event=language_fallback``) so the team can see where English is filling
  in. Even a broken *override* falls back: a non-English template that fails
  to render logs the failure and renders English instead.
- **A broken config refuses to boot.** Everything checkable without a turn is
  checked at load: the YAML's shape, that ``en`` exists, that every template
  parses, that every template uses all of its component's declared
  ``placeholders`` and nothing else (``str.format``'s silently-dropped
  placeholder is the bug this guards against — see the old
  ``core/planner/prompt._checked``), and that every skill file parses.

Rendering is Jinja2 with ``StrictUndefined``: a kwarg the template needs but
the caller did not send raises instead of printing nothing.

Path resolution: relative paths inside the YAML resolve against the YAML's
parent directory's parent — ``configs/prompts.yaml`` beside ``prompts/`` at
the repo root, or wherever a deployment mounts the two side by side.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, Template, meta

from dss.config.skill_loader import load_skill_file
from dss.core.planner.models import Skill
from dss.observability.trace_log import log_event

logger = logging.getLogger(__name__)

# Relative to the working directory, like `evidence_dir`: the repo is run from
# its root (docs/RUNNING.md), and the image sets WORKDIR /app and copies both
# directories there. A different layout sets DSS_PROMPT_CONFIG_PATH.
DEFAULT_CONFIG = Path("configs/prompts.yaml")

# The language every component must ship and every turn can fall back to.
FALLBACK_LANGUAGE = "en"


@dataclass(frozen=True)
class _LanguageEntry:
    """One language's row for one component: the compiled template and the
    skills configured beside it."""

    template: Template
    skills: tuple[Skill, ...]


@dataclass(frozen=True)
class _ComponentPrompts:
    """One component's row: its declared placeholders and its languages."""

    placeholders: frozenset[str]
    languages: dict[str, _LanguageEntry]


class PromptService:
    """Serves each component's system prompt in the turn's language, falling
    back to English when that language has no version. Satisfies
    ``ports.prompts.PromptProvider``."""

    def __init__(self, components: dict[str, _ComponentPrompts]) -> None:
        self._components = components

    def get_prompt(
        self,
        prompt_identifier: str,
        lang: str,
        kwargs: Mapping[str, Any] | None = None,
    ) -> str:
        name = self._name(prompt_identifier)
        component = self._components[name]
        served = self._resolve(name, component, lang)
        values = dict(kwargs or {})
        try:
            return component.languages[served].template.render(**values)
        except Exception:
            # An override must never cost the farmer an answer. English has
            # no further fallback, so a failure there propagates — it is a
            # code/config defect, not a language gap.
            if served == FALLBACK_LANGUAGE:
                raise
            logger.exception(
                "the %s prompt for %r failed to render; serving English instead",
                name,
                served,
            )
            return component.languages[FALLBACK_LANGUAGE].template.render(**values)

    def get_skills(self, prompt_identifier: str, lang: str) -> tuple[Skill, ...]:
        name = self._name(prompt_identifier)
        component = self._components[name]
        served = self._resolve(name, component, lang)
        skills = component.languages[served].skills
        if skills or served == FALLBACK_LANGUAGE:
            return skills
        # A language entry may override the prompt alone. Its skills then come
        # from English — "each prompt falls back on its own" holds per file.
        return component.languages[FALLBACK_LANGUAGE].skills

    def languages_for(self, prompt_identifier: str) -> tuple[str, ...]:
        """Which languages have their own version of this prompt. For
        visibility (logged at boot), not for routing."""

        name = self._name(prompt_identifier)
        return tuple(sorted(self._components[name].languages))

    def _name(self, prompt_identifier: str) -> str:
        name = prompt_identifier.lower()
        if name not in self._components:
            raise KeyError(
                f"no prompt is configured for {prompt_identifier!r} — "
                f"configs/prompts.yaml knows {sorted(self._components)}"
            )
        return name

    def _resolve(self, name: str, component: _ComponentPrompts, lang: str) -> str:
        """Exact tag, then primary subtag, then English — recording the fall
        to English as a trace event, because a deployment reads that event to
        see which languages still run on the English prompt."""

        tag = lang.lower()
        if tag in component.languages:
            return tag
        primary = tag.split("-", 1)[0]
        if primary in component.languages:
            return primary
        if primary != FALLBACK_LANGUAGE:
            log_event(
                "prompts",
                event="language_fallback",
                prompt=name,
                requested=lang,
                served=FALLBACK_LANGUAGE,
            )
        return FALLBACK_LANGUAGE


def load_prompt_service(path: Path | None = None) -> PromptService:
    """Load and validate the prompt registry; raise on anything broken.

    - ``path`` unset → ``configs/prompts.yaml`` under the working directory.
    - ``path`` set but missing → ``FileNotFoundError`` (I have a config + it
      isn't there; do not boot on a different one).
    """

    source = DEFAULT_CONFIG if path is None else path
    if not source.exists():
        raise FileNotFoundError(
            f"prompt config path is set to {source} but no file is there — "
            "refusing to boot on a different configuration"
        )

    base = source.resolve().parent.parent
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{source} holds no components — expected a mapping")

    environment = Environment(undefined=StrictUndefined, autoescape=False)
    components: dict[str, _ComponentPrompts] = {}
    for name, spec in data.items():
        components[str(name).lower()] = _load_component(
            environment, base, str(name), spec
        )

    for name, component in components.items():
        logger.info(
            "prompt %s: languages %s",
            name,
            ", ".join(sorted(component.languages)),
        )
    return PromptService(components)


def _load_component(
    environment: Environment, base: Path, name: str, spec: Any
) -> _ComponentPrompts:
    if not isinstance(spec, dict):
        raise ValueError(f"prompt {name!r}: expected a mapping, got {type(spec)}")
    placeholders = frozenset(spec.get("placeholders") or ())
    language_prompts = spec.get("language_prompts")
    if not isinstance(language_prompts, dict) or not language_prompts:
        raise ValueError(f"prompt {name!r} declares no language_prompts")

    languages = {
        str(tag).lower(): _load_language(
            environment, base, f"{name}/{tag}", placeholders, entry
        )
        for tag, entry in language_prompts.items()
    }
    if FALLBACK_LANGUAGE not in languages:
        raise ValueError(
            f"prompt {name!r} has no '{FALLBACK_LANGUAGE}' entry — every "
            "component needs the English prompt, because it is what every "
            "other language falls back to"
        )
    return _ComponentPrompts(placeholders=placeholders, languages=languages)


def _load_language(
    environment: Environment,
    base: Path,
    label: str,
    placeholders: frozenset[str],
    entry: Any,
) -> _LanguageEntry:
    if not isinstance(entry, dict) or "system_prompt" not in entry:
        raise ValueError(f"prompt {label}: each language needs a system_prompt path")

    template_path = _resolved(base, entry["system_prompt"])
    if not template_path.exists():
        raise FileNotFoundError(
            f"prompt {label} points at {template_path} but no file is there"
        )
    text = template_path.read_text(encoding="utf-8")

    used = meta.find_undeclared_variables(
        environment.parse(text, filename=str(template_path))
    )
    missing = placeholders - used
    if missing:
        raise ValueError(
            f"prompt {label} ({template_path}) never uses "
            f"{', '.join(sorted(missing))} — the rendered prompt would "
            "silently drop it"
        )
    unknown = used - placeholders
    if unknown:
        raise ValueError(
            f"prompt {label} ({template_path}) uses "
            f"{', '.join(sorted(unknown))}, which the code does not supply — "
            "declare it in placeholders only if every caller sends it"
        )

    skills = tuple(
        load_skill_file(_resolved(base, skill_path))
        for skill_path in entry.get("skills") or ()
    )
    return _LanguageEntry(template=environment.from_string(text), skills=skills)


def _resolved(base: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else base / candidate
