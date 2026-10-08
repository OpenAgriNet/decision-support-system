"""Tier 1 — the skill file parser/loader.

The shipped skill itself now ships under ``prompts/planner/<lang>/skills/``,
named by ``configs/prompts.yaml``, and is pinned in
``test_shipped_prompts.py``; this file covers only how a file becomes a
``Skill``."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.skill_loader import load_skills


def test_guidance_is_the_markdown_body(tmp_path: Path) -> None:
    skill_file = tmp_path / "test-skill.md"
    skill_file.write_text(
        "---\n"
        "id: test-skill\n"
        "domain: agriculture\n"
        "description: A test skill.\n"
        "tool_names: [select]\n"
        "---\n"
        "Guidance line one.\n"
        "Guidance line two.\n",
        encoding="utf-8",
    )

    skills = load_skills(tmp_path)

    assert len(skills) == 1
    assert skills[0].guidance == "Guidance line one.\nGuidance line two.\n"


def test_set_but_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_skills(tmp_path / "does-not-exist")


def test_a_skill_file_with_crlf_line_endings_loads(tmp_path: Path) -> None:
    """A skill edited on Windows has ``---\\r\\n`` delimiters, which the
    ``---\\n`` split would not match.

    It works because ``read_text`` does universal-newline translation, so the
    split never sees a ``\\r``. Pinned here because that is easy to break —
    reading bytes, or opening with ``newline=""``, would reintroduce it."""

    (tmp_path / "windows-edited.md").write_bytes(
        b"---\r\n"
        b"id: windows-edited\r\n"
        b"domain: agriculture\r\n"
        b"description: A skill saved with CRLF.\r\n"
        b"tool_names: [select]\r\n"
        b"---\r\n"
        b"Guidance line one.\r\n"
    )

    skills = load_skills(tmp_path)

    assert [skill.id for skill in skills] == ["windows-edited"]
    assert "Guidance line one." in skills[0].guidance


def test_a_file_with_no_frontmatter_names_the_file(tmp_path: Path) -> None:
    """The loader's stance is "do not boot on a broken config" — but it has
    to say *which* config. A bare unpack error names no file, and a
    deployment can mount many skills."""

    (tmp_path / "forgot-frontmatter.md").write_text(
        "Just some guidance, no frontmatter.\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="forgot-frontmatter.md"):
        load_skills(tmp_path)
