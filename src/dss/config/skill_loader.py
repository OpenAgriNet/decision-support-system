"""Loads skills from markdown files — one file per skill, YAML frontmatter for
metadata and the markdown body as ``guidance``.

Hand-rolled frontmatter split rather than a new dependency: the format is
``---\\n<yaml>\\n---\\n<body>``, and pyyaml is already a dependency.

Which skill files a deployment uses is named in ``configs/prompts.yaml`` —
per component, per language — and loaded through ``prompt_service``. This
module only knows how to turn one file (or one directory of them) into
``Skill`` objects.

Mirrors ``policy_loader.py``: a configured-but-missing path raises rather than
silently falling back to a different configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.planner.models import Skill

_FRONTMATTER_DELIMITER = "---\n"


def parse_skill_file(text: str, name: str) -> Skill:
    """Parse one ``---``-delimited skill file.

    ``name`` is only for the error. A bare unpack or ``KeyError`` names no
    file, and a deployment can mount many skills — "do not boot on a broken
    config" has to say *which* config.
    """

    parts = text.split(_FRONTMATTER_DELIMITER, 2)
    if len(parts) != 3:
        raise ValueError(
            f"{name} has no '---' frontmatter block — a skill file is "
            "'---', YAML metadata, '---', then the guidance"
        )

    _, frontmatter, body = parts
    metadata = yaml.safe_load(frontmatter) or {}
    missing = [
        key
        for key in ("id", "domain", "description", "tool_names")
        if key not in metadata
    ]
    if missing:
        raise ValueError(f"{name} is missing {', '.join(missing)} in its frontmatter")

    return Skill(
        id=metadata["id"],
        domain=metadata["domain"],
        description=metadata["description"],
        tool_names=tuple(metadata["tool_names"]),
        guidance=body,
    )


def load_skill_file(path: Path) -> Skill:
    """Load one skill file, raising if it is not there — a config that names
    a file that does not exist must refuse to boot, not boot without it."""

    if not path.exists():
        raise FileNotFoundError(
            f"a skill file is configured at {path} but no file is there — "
            "refusing to boot on a different configuration"
        )
    return parse_skill_file(path.read_text(encoding="utf-8"), path.name)


def load_skills(path: Path) -> tuple[Skill, ...]:
    """Load every skill in the directory ``path``.

    ``path`` set but missing → ``FileNotFoundError`` (I have a config + it
    isn't there; do not boot on a different one).
    """

    if not path.exists():
        raise FileNotFoundError(
            f"skills config path is set to {path} but no directory is there — "
            "refusing to boot on a different configuration"
        )

    return tuple(
        parse_skill_file(skill_file.read_text(encoding="utf-8"), skill_file.name)
        for skill_file in sorted(path.glob("*.md"))
    )
