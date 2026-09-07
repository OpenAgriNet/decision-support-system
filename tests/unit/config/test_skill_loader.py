"""Tier 1 — the skill loader and the shipped default skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.skill_loader import load_skills


def test_default_skills_load_and_validate() -> None:
    skills = load_skills()
    ids = {s.id for s in skills}
    assert ids == {"provider-invocation"}


def test_provider_invocation_skill_shape() -> None:
    skills = load_skills()
    skill = next(s for s in skills if s.id == "provider-invocation")
    assert skill.domain == "agriculture"
    assert skill.tool_names == ("describe_capability", "select")
    assert skill.description
    assert skill.guidance


def test_provider_invocation_guidance_covers_resolving_across_history() -> None:
    """A follow-up turn carries the subject in an earlier message: "advisory
    for potato" ... "I am from Pune". Filling fields from the last message
    alone loses the subject, so the guidance has to say to read the whole
    conversation.

    Asserts on substance, not wording — the phrasing is the model's to read,
    but "use the earlier messages" must be in there somewhere."""

    skills = load_skills()
    raw = next(s for s in skills if s.id == "provider-invocation").guidance
    # collapse the file's line wrapping so a phrase split across two lines
    # still matches
    guidance = " ".join(raw.lower().split())

    assert "conversation" in guidance or "earlier" in guidance
    assert "not just the last message" in guidance


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
