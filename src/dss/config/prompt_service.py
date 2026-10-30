"""The prompt service — the ``ports.prompts.PromptProvider`` implementation.

``configs/prompts.yaml`` names, per LLM component and per language, which
template under ``prompts/`` is the system prompt (and, for the planner, which
skill files ride with it). This module loads that registry once at boot and
answers ``get_prompt(PromptIdentifier.INTENT, lang=LanguageTag("hi"),
placeholder_values={...})`` per turn.

The two properties the design leans on:

- **A missing override never fails a turn.** ``en`` is required for every
  component and is always loadable, so a turn in a language with no entry runs
  on the English prompt. The gap is recorded as a trace event
  (``event=language_fallback``) so the team can see where English is filling
  in. Even a broken *override* falls back: a non-English template that fails
  to render logs the failure and renders English instead.
- **A broken config refuses to boot.** Everything checkable without a turn is
  checked at load: the YAML's shape (pydantic, unknown keys forbidden), that
  every ``PromptIdentifier`` has an entry and each has ``en``, that every
  template parses, that every template uses all of its component's declared
  ``placeholders`` and nothing else (``str.format``'s silently-dropped
  placeholder is the bug this guards against — see the old
  ``core/planner/prompt._checked``), and that every skill file parses.

Rendering is Jinja2 with ``StrictUndefined``: a value the template needs but
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
from pydantic import BaseModel, ConfigDict, ValidationError

from dss.config.skill_loader import load_skill_file
from dss.core.planner.models import Skill
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.observability.trace_log import log_event
from dss.ports.prompts import PromptIdentifier

logger = logging.getLogger(__name__)

# Relative to the working directory, like `evidence_dir`: the repo is run from
# its root (docs/PLAYBOOK.md), and the image sets WORKDIR /app and copies both
# directories there. A different layout sets DSS_PROMPT_CONFIG_PATH.
DEFAULT_CONFIG = Path("configs/prompts.yaml")

# The language every component must ship and every turn can fall back to.
FALLBACK_LANGUAGE = ENGLISH


class _LanguageSpec(BaseModel):
    """One language's row in the YAML, as written — paths, not content.

    ``extra="forbid"``: a misspelt key (``skils:``) refuses to boot instead of
    silently shipping a prompt without its skills."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: str
    skills: tuple[str, ...] = ()


class _ComponentSpec(BaseModel):
    """One component's row in the YAML, same stance as ``_LanguageSpec``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    placeholders: frozenset[str] = frozenset()
    language_prompts: dict[LanguageTag, _LanguageSpec]


@dataclass(frozen=True)
class _LanguageEntry:
    """One language's row for one component, loaded: the compiled template and
    the skills configured beside it."""

    template: Template
    skills: tuple[Skill, ...]


@dataclass(frozen=True)
class _ComponentPrompts:
    """One component's row, loaded: its declared placeholders and its
    languages."""

    placeholders: frozenset[str]
    languages: dict[LanguageTag, _LanguageEntry]


class PromptService:
    """Serves each component's system prompt in the turn's language, falling
    back to English when that language has no version. Satisfies
    ``ports.prompts.PromptProvider``."""

    def __init__(self, components: dict[str, _ComponentPrompts]) -> None:
        self._components = components

    def get_prompt(
        self,
        prompt_identifier: PromptIdentifier,
        lang: LanguageTag,
        placeholder_values: Mapping[str, Any] | None = None,
    ) -> str:
        component = self._components[prompt_identifier.value]
        served = self._resolve(prompt_identifier, component, lang)
        values = placeholder_values or {}
        try:
            return component.languages[served].template.render(**values)
        except Exception:
            # An override must never cost the farmer an answer. English has
            # no further fallback, so a failure there propagates — it is a
            # code/config defect, not a language gap.
            if served == FALLBACK_LANGUAGE:
                raise
            logger.exception(
                "the %s prompt for %s failed to render; serving English instead",
                prompt_identifier.value,
                served,
            )
            return component.languages[FALLBACK_LANGUAGE].template.render(**values)

    def get_skills(
        self, prompt_identifier: PromptIdentifier, lang: LanguageTag
    ) -> tuple[Skill, ...]:
        component = self._components[prompt_identifier.value]
        served = self._resolve(prompt_identifier, component, lang)
        skills = component.languages[served].skills
        if skills or served == FALLBACK_LANGUAGE:
            return skills
        # A language entry may override the prompt alone. Its skills then come
        # from English — "each prompt falls back on its own" holds per file.
        return component.languages[FALLBACK_LANGUAGE].skills

    def languages_for(
        self, prompt_identifier: PromptIdentifier
    ) -> tuple[LanguageTag, ...]:
        """Which languages have their own version of this prompt. For
        visibility (logged at boot), not for routing."""

        return tuple(
            sorted(self._components[prompt_identifier.value].languages, key=str)
        )

    def _resolve(
        self,
        prompt_identifier: PromptIdentifier,
        component: _ComponentPrompts,
        lang: LanguageTag,
    ) -> LanguageTag:
        """The most specific form of the tag the registry has, then English —
        recording the fall to English as a trace event, because a deployment
        reads that event to see which languages still run on the English
        prompt."""

        served = next(
            (tag for tag in lang.lookup_order if tag in component.languages), None
        )
        if served is not None:
            return served
        # Nothing matched. `en` always has an entry (checked at boot), so the
        # request was non-English — record the gap, unconditionally.
        log_event(
            "prompts",
            event="language_fallback",
            prompt=prompt_identifier.value,
            requested=str(lang),
            served=str(FALLBACK_LANGUAGE),
        )
        return FALLBACK_LANGUAGE


def build_prompt_service(path: Path | None = None) -> PromptService:
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

    environment = Environment(undefined=StrictUndefined, autoescape=False)  # noqa: S701 - prompts are plain text, not HTML
    components = {
        str(name).lower(): _load_component(environment, base, str(name), raw)
        for name, raw in data.items()
    }

    # Every identifier the code can ask for must be served, or the miss would
    # surface as a KeyError mid-turn instead of a refusal at boot.
    unserved = [
        identifier.value
        for identifier in PromptIdentifier
        if identifier.value not in components
    ]
    if unserved:
        raise ValueError(
            f"{source} has no entry for {', '.join(unserved)} — every LLM "
            "component needs its prompt, refusing to boot"
        )

    for name, component in components.items():
        logger.info(
            "prompt %s: languages %s",
            name,
            ", ".join(sorted(map(str, component.languages))),
        )
    return PromptService(components)


def _load_component(
    environment: Environment, base: Path, name: str, raw: object
) -> _ComponentPrompts:
    try:
        spec = _ComponentSpec.model_validate(raw)
    except ValidationError as error:
        raise ValueError(f"prompt {name!r}: {error}") from error

    languages = {
        language_tag: _load_language(
            environment, base, f"{name}/{language_tag}", spec.placeholders, entry
        )
        for language_tag, entry in spec.language_prompts.items()
    }
    if FALLBACK_LANGUAGE not in languages:
        raise ValueError(
            f"prompt {name!r} has no '{FALLBACK_LANGUAGE}' entry — every "
            "component needs the English prompt, because it is what every "
            "other language falls back to"
        )
    return _ComponentPrompts(placeholders=spec.placeholders, languages=languages)


def _load_language(
    environment: Environment,
    base: Path,
    label: str,
    placeholders: frozenset[str],
    entry: _LanguageSpec,
) -> _LanguageEntry:
    template_path = _resolved(base, entry.system_prompt)
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
        load_skill_file(_resolved(base, skill_path)) for skill_path in entry.skills
    )
    return _LanguageEntry(template=environment.from_string(text), skills=skills)


def _resolved(base: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else base / candidate
