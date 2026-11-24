"""The capability tools service — the ``ports.capability_tools`` implementation.

The planner's Agent Skills live in one folder (``capability_tools/`` by
default, ``DSS_CAPABILITY_TOOLS_DIR`` otherwise): a folder per language, each
holding one ``<category in lowercase>/SKILL.md`` per intent category. This
module checks that layout once at boot and answers, per turn, which skills
the turn's categories offer. The model reads the skill files themselves, so
they are not read here.

A broken layout refuses to boot, as a broken prompt registry does
(ADR-0018): a set folder that is not there, a folder not named for a
language tag, or a skill not named for an intent category would otherwise
never be offered, and nobody would notice.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from dss.core.intent.models import SubjectCategory
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.ports.capability_tools import CapabilityTools

# What marks a folder in a language's folder as an Agent Skill.
_SKILL_FILE = "SKILL.md"


@dataclass(frozen=True)
class _Library:
    """One language's folder, loaded: which skill serves which category."""

    path: Path
    skill_names: Mapping[SubjectCategory, str]


class CapabilityToolsService:
    """Serves the skills for a turn's categories in the turn's language,
    falling back to English. Satisfies
    ``ports.capability_tools.CapabilityToolProvider``."""

    def __init__(self, libraries: Mapping[LanguageTag, _Library]) -> None:
        self._libraries = libraries

    def get_capability_tools(
        self, lang: LanguageTag, categories: Collection[SubjectCategory]
    ) -> CapabilityTools | None:
        library = next(
            (
                self._libraries[tag]
                for tag in (*lang.lookup_order, ENGLISH)
                if tag in self._libraries
            ),
            None,
        )
        if library is None:
            return None
        names = tuple(
            name
            for category, name in library.skill_names.items()
            if category in categories
        )
        if not names:
            return None
        return CapabilityTools(library_path=str(library.path), names=names)


def build_capability_tools_service(root: Path) -> CapabilityToolsService:
    """Load and check the folder; raise on anything broken. A set folder
    that is not there raises ``FileNotFoundError``: do not boot without the
    skills the deployment was given."""

    if not root.is_dir():
        raise FileNotFoundError(
            f"capability tools dir is set to {root} but no folder is there — "
            "refusing to boot without the skills it was given"
        )
    return CapabilityToolsService(
        {
            _language(folder): _load_library(folder.resolve())
            for folder in sorted(root.iterdir())
            if folder.is_dir()
        }
    )


def _language(folder: Path) -> LanguageTag:
    try:
        return LanguageTag(folder.name)
    except ValidationError:
        raise ValueError(
            f"capability tools folder {folder} is not named for a language tag"
        ) from None


def _load_library(path: Path) -> _Library:
    label = f"{path.parent.name}/{path.name}"
    return _Library(
        path=path,
        skill_names={
            _category(folder.name, label): folder.name
            for folder in sorted(path.iterdir())
            if (folder / _SKILL_FILE).is_file()
        },
    )


def _category(skill_name: str, label: str) -> SubjectCategory:
    """A skill named for no intent category would never be offered — so it
    refuses to boot rather than sit there unread."""

    by_skill_name = {category.value.lower(): category for category in SubjectCategory}
    try:
        return by_skill_name[skill_name]
    except KeyError:
        raise ValueError(
            f"capability tools {label} has skill {skill_name!r}, which is not "
            f"an intent category in lowercase ({', '.join(by_skill_name)})"
        ) from None
