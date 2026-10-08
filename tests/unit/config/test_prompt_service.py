"""The prompt service: the registry's validation and the language fallback.

The two properties the multilingual design rests on, pinned here:

- a broken registry refuses to boot (missing file, missing ``en``, a template
  dropping a declared placeholder or using an undeclared one);
- a missing or broken *override* never fails a turn — it serves English and
  records the gap.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from jinja2 import UndefinedError

from dss.config.prompt_service import load_prompt_service

_SKILL = """---
id: test-skill
domain: agriculture
description: A test skill.
tool_names: [select]
---
Guidance line one.
"""


def _registry(
    tmp_path: Path,
    *,
    en_template: str = "Hello {{ name }}",
    hi_template: str | None = "नमस्ते {{ name }}",
    placeholders: tuple[str, ...] = ("name",),
    en_skills: bool = False,
    include_en: bool = True,
) -> Path:
    """Write a one-component registry under ``tmp_path`` and return the YAML's
    path. Laid out like the real one — ``configs/prompts.yaml`` beside
    ``prompts/`` — because relative paths resolve against the YAML's parent's
    parent."""

    (tmp_path / "configs").mkdir()
    lines = ["greeting:", "  placeholders:"]
    lines += [f"    - {p}" for p in placeholders]
    lines += ["  language_prompts:"]
    for tag, template in (("en", en_template), ("hi", hi_template)):
        if template is None or (tag == "en" and not include_en):
            continue
        path = tmp_path / "prompts" / "greeting" / tag / "prompt.j2"
        path.parent.mkdir(parents=True)
        path.write_text(template, encoding="utf-8")
        lines += [
            f"    {tag}:",
            f"      system_prompt: prompts/greeting/{tag}/prompt.j2",
        ]
        if tag == "en" and en_skills:
            skill = tmp_path / "prompts" / "greeting" / "en" / "skill.md"
            skill.write_text(_SKILL, encoding="utf-8")
            lines += ["      skills:", "        - prompts/greeting/en/skill.md"]
    config = tmp_path / "configs" / "prompts.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def test_the_configured_language_is_served() -> None:
    # the shipped registry, which is also what a deployment edits
    service = load_prompt_service(
        Path(__file__).resolve().parents[3] / "configs" / "prompts.yaml"
    )
    prompt = service.get_prompt(
        "COMPOSER",
        lang="hi",
        kwargs={"name": "n", "persona": "p", "boundaries": "b", "target_lang": "hi"},
    )
    assert "किसान" in prompt  # the Hindi demo, not the English fallback


def test_a_language_with_no_entry_falls_back_to_english(tmp_path: Path) -> None:
    service = load_prompt_service(_registry(tmp_path))
    assert service.get_prompt("GREETING", "mr", {"name": "x"}) == "Hello x"


def test_the_fallback_is_recorded_as_a_trace_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The team reads this event to see which languages still run on the
    English prompt — the gap is a fact about the deployment, not an error."""

    service = load_prompt_service(_registry(tmp_path))

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        service.get_prompt("GREETING", "mr", {"name": "x"})

    line = next(r.message for r in caplog.records if "language_fallback" in r.message)
    assert "prompt=greeting" in line
    assert "requested=mr" in line
    assert "served=en" in line


def test_a_regional_tag_resolves_to_its_primary_subtag(tmp_path: Path) -> None:
    service = load_prompt_service(_registry(tmp_path))
    assert service.get_prompt("GREETING", "hi-IN", {"name": "x"}) == "नमस्ते x"


def test_identifier_lookup_is_case_insensitive(tmp_path: Path) -> None:
    service = load_prompt_service(_registry(tmp_path))
    assert service.get_prompt("greeting", "en", {"name": "x"}) == "Hello x"


def test_an_unknown_identifier_raises(tmp_path: Path) -> None:
    """A component asking for a prompt the registry does not know is a wiring
    defect, not a language gap — there is no English version to fall back to."""

    service = load_prompt_service(_registry(tmp_path))
    with pytest.raises(KeyError, match="NO-SUCH-PROMPT"):
        service.get_prompt("NO-SUCH-PROMPT", "en", {})


def test_a_broken_override_serves_english_rather_than_failing_the_turn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Load-time checks catch a template using an undeclared variable, but an
    attribute miss inside a declared one only surfaces at render. Even then an
    override must not cost the farmer an answer."""

    config = _registry(tmp_path, hi_template="नमस्ते {{ name.missing_attr }}")
    service = load_prompt_service(config)

    with caplog.at_level(logging.ERROR):
        prompt = service.get_prompt("GREETING", "hi", {"name": "x"})

    assert prompt == "Hello x"
    assert any("serving English instead" in r.message for r in caplog.records)


def test_a_broken_english_template_propagates(tmp_path: Path) -> None:
    """English has no further fallback: a render failure there is a defect to
    surface, not a gap to paper over."""

    config = _registry(tmp_path, en_template="Hello {{ name.missing_attr }}")
    service = load_prompt_service(config)

    with pytest.raises(UndefinedError):
        service.get_prompt("GREETING", "en", {"name": "x"})


# --- boot-time validation ---------------------------------------------------


def test_a_set_but_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt_service(tmp_path / "does-not-exist.yaml")


def test_a_component_without_english_refuses_to_boot(tmp_path: Path) -> None:
    config = _registry(tmp_path, include_en=False)
    with pytest.raises(ValueError, match="'en'"):
        load_prompt_service(config)


def test_a_template_dropping_a_declared_placeholder_refuses_to_boot(
    tmp_path: Path,
) -> None:
    """The silent-drop bug ``str.format`` had: a typo'd placeholder would ship
    a prompt with no identity in it, and nothing would notice."""

    config = _registry(tmp_path, hi_template="नमस्ते {{ nmae }}, {{ name }}")
    with pytest.raises(ValueError, match="nmae"):
        load_prompt_service(config)


def test_a_template_missing_a_declared_placeholder_refuses_to_boot(
    tmp_path: Path,
) -> None:
    config = _registry(tmp_path, hi_template="नमस्ते without the name")
    with pytest.raises(ValueError, match="never uses name"):
        load_prompt_service(config)


def test_a_named_template_file_that_is_absent_refuses_to_boot(tmp_path: Path) -> None:
    config = _registry(tmp_path)
    (tmp_path / "prompts" / "greeting" / "hi" / "prompt.j2").unlink()
    with pytest.raises(FileNotFoundError):
        load_prompt_service(config)


# --- skills -----------------------------------------------------------------


def test_skills_load_with_the_prompt(tmp_path: Path) -> None:
    service = load_prompt_service(_registry(tmp_path, en_skills=True))
    skills = service.get_skills("GREETING", "en")
    assert [s.id for s in skills] == ["test-skill"]


def test_a_language_entry_without_skills_falls_back_to_english_skills(
    tmp_path: Path,
) -> None:
    """An override may replace the prompt alone; the agent still needs tools.
    "Each prompt falls back on its own" holds per file."""

    service = load_prompt_service(_registry(tmp_path, en_skills=True))
    assert service.get_skills("GREETING", "hi") == service.get_skills("GREETING", "en")


def test_a_component_without_skills_has_none(tmp_path: Path) -> None:
    service = load_prompt_service(_registry(tmp_path))
    assert service.get_skills("GREETING", "en") == ()
