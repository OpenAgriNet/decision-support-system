"""Loads skills from markdown files — one file per skill, YAML frontmatter for
metadata and the markdown body as ``guidance``.

Hand-rolled frontmatter split rather than a new dependency: the format is
``---\\n<yaml>\\n---\\n<body>``, and pyyaml is already a dependency.

Mirrors ``policy_loader.py``: a configured-but-missing path raises rather than
silently falling back to a different configuration.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from dss.core.planner.models import Skill

# The skills that ship in the image. Adopters mount their own directory via
# DSS_SKILLS_CONFIG_PATH; absent that, this is used.
_DEFAULTS = Path(__file__).parent / "defaults" / "skills"

_FRONTMATTER_DELIMITER = "---\n"


def _parse_skill_file(text: str) -> Skill:
    _, frontmatter, body = text.split(_FRONTMATTER_DELIMITER, 2)
    metadata = yaml.safe_load(frontmatter)
    return Skill(
        id=metadata["id"],
        domain=metadata["domain"],
        description=metadata["description"],
        tool_names=tuple(metadata["tool_names"]),
        guidance=body,
    )


def load_skills(path: Path | None = None) -> tuple[Skill, ...]:
    """Load every skill in ``path`` (or the bundled defaults).

    - ``path`` unset → bundled defaults (I have no custom config).
    - ``path`` set but missing → ``FileNotFoundError`` (I have a config + it
      isn't there; do not boot on a different one).
    """

    source = _DEFAULTS if path is None else path
    if not source.exists():
        raise FileNotFoundError(
            f"skills config path is set to {source} but no directory is there — "
            "refusing to boot on a different configuration"
        )

    return tuple(
        _parse_skill_file(skill_file.read_text(encoding="utf-8"))
        for skill_file in sorted(source.glob("*.md"))
    )
