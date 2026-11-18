"""The prompt service: the registry's validation and the language fallback.

The two properties the multilingual design rests on, pinned here:

- a broken registry refuses to boot (missing file, missing component, missing
  ``en``, a template dropping a declared placeholder or using an undeclared
  one);
- a missing or broken *override* never fails a turn — it serves English and
  records the gap.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from jinja2 import UndefinedError

from dss.config.prompt_service import build_prompt_service
from dss.core.intent.models import SubjectCategory
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.ports.prompts import PromptIdentifier

_SKILL_FRONTMATTER = """---
id: test-skill
domain: agriculture
description: A test skill.
tool_names: [select]
---
"""

# Any member does for probing behaviour that is the same for every component.
_PROBE = PromptIdentifier.INTENT


def _registry(
    tmp_path: Path,
    *,
    en_template: str = "Hello {{ name }}",
    override_template: str | None = "नमस्ते {{ name }}",
    override_key: str = "hi",
    more_overrides: tuple[tuple[str, str], ...] = (),
    placeholders: tuple[str, ...] = ("name",),
    en_skills: bool = False,
    skill_guidance: str = "Guidance line one.\n\n{{ category_guidance }}\n",
    en_category_guidance: tuple[tuple[str, str], ...] = (),
    include_en: bool = True,
    omit_component: str | None = None,
) -> Path:
    """Write a complete registry under ``tmp_path`` and return the YAML's path.

    Every ``PromptIdentifier`` gets an entry (boot requires it), all sharing
    the same template files — the behaviour under test is the service's, not
    any one component's. Laid out like the real one — ``configs/prompts.yaml``
    beside ``prompts/`` — because relative paths resolve against the YAML's
    parent's parent."""

    (tmp_path / "configs").mkdir()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    languages: list[str] = []
    entries = (("en", en_template), (override_key, override_template), *more_overrides)
    for language_tag, template in entries:
        if template is None or (language_tag == "en" and not include_en):
            continue
        (prompt_dir / f"{language_tag}.j2").write_text(template, encoding="utf-8")
        languages.append(language_tag)
    if en_skills:
        (prompt_dir / "skill.md").write_text(
            _SKILL_FRONTMATTER + skill_guidance, encoding="utf-8"
        )
    for category, guidance in en_category_guidance:
        (prompt_dir / f"{category}.md").write_text(guidance, encoding="utf-8")

    lines: list[str] = []
    for identifier in PromptIdentifier:
        if identifier.value == omit_component:
            continue
        lines += [f"{identifier.value}:", "  placeholders:"]
        lines += [f"    - {placeholder}" for placeholder in placeholders]
        lines += ["  language_prompts:"]
        for language_tag in languages:
            lines += [
                f"    {language_tag}:",
                f"      system_prompt: prompts/{language_tag}.j2",
            ]
            if language_tag == "en" and en_skills:
                lines += ["      skills:", "        - prompts/skill.md"]
            if language_tag == "en" and en_category_guidance:
                lines += ["      category_guidance:"]
                lines += [
                    f"        {category}: prompts/{category}.md"
                    for category, _ in en_category_guidance
                ]
    config = tmp_path / "configs" / "prompts.yaml"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def test_the_configured_language_is_served() -> None:
    # the shipped registry, which is also what a deployment edits
    service = build_prompt_service(
        Path(__file__).resolve().parents[3] / "configs" / "prompts.yaml"
    )
    prompt = service.get_prompt(
        PromptIdentifier.COMPOSER,
        lang=LanguageTag("hi"),
        placeholder_values={
            "name": "n",
            "persona": "p",
            "boundaries": "b",
            "target_lang": "hi",
            "category_guidance": "",
        },
    )
    assert "किसान" in prompt  # the Hindi demo, not the English fallback


def test_a_language_with_no_entry_falls_back_to_english(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path))
    assert service.get_prompt(_PROBE, LanguageTag("mr"), {"name": "x"}) == "Hello x"


def test_a_language_tag_renders_as_its_plain_tag(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path))
    rendered = service.get_prompt(_PROBE, ENGLISH, {"name": LanguageTag("gu-in")})
    assert rendered == "Hello gu-IN"


def test_the_fallback_is_recorded_as_a_trace_event(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The team reads this event to see which languages still run on the
    English prompt — the gap is a fact about the deployment, not an error."""

    service = build_prompt_service(_registry(tmp_path))

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        service.get_prompt(_PROBE, LanguageTag("mr"), {"name": "x"})

    line = next(
        r for r in caplog.records if getattr(r, "event", None) == "language_fallback"
    )
    assert (line.prompt, line.requested, line.served) == ("intent", "mr", "en")


def test_a_regional_tag_resolves_to_its_primary_subtag(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path))
    assert service.get_prompt(_PROBE, LanguageTag("hi-IN"), {"name": "x"}) == "नमस्ते x"


def test_a_registry_key_is_read_as_a_tag(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path, override_key="HI"))
    assert service.get_prompt(_PROBE, LanguageTag("hi-IN"), {"name": "x"}) == "नमस्ते x"


def test_a_script_tag_is_served_before_its_language(tmp_path: Path) -> None:
    config = _registry(
        tmp_path,
        override_key="gu",
        override_template="GU {{ name }}",
        more_overrides=(("gu-Gujr", "GUJR {{ name }}"),),
    )
    service = build_prompt_service(config)
    assert service.get_prompt(_PROBE, LanguageTag("gu-Gujr-IN"), {"name": "x"}) == (
        "GUJR x"
    )


def test_a_three_part_tag_walks_down_to_its_language(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path, override_key="gu"))
    assert (
        service.get_prompt(_PROBE, LanguageTag("gu-Gujr-IN"), {"name": "x"}) == "नमस्ते x"
    )


def test_an_english_regional_tag_logs_no_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """en-GB resolves to en through the primary subtag — a match, not a gap,
    so nothing is recorded."""

    service = build_prompt_service(_registry(tmp_path))

    with caplog.at_level(logging.INFO, logger="dss.trace"):
        assert (
            service.get_prompt(_PROBE, LanguageTag("en-GB"), {"name": "x"}) == "Hello x"
        )

    assert not any("language_fallback" in r.message for r in caplog.records)


def test_a_broken_override_serves_english_rather_than_failing_the_turn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Load-time checks catch a template using an undeclared variable, but an
    attribute miss inside a declared one only surfaces at render. Even then an
    override must not cost the farmer an answer."""

    config = _registry(tmp_path, override_template="नमस्ते {{ name.missing_attr }}")
    service = build_prompt_service(config)

    with caplog.at_level(logging.ERROR):
        prompt = service.get_prompt(_PROBE, LanguageTag("hi"), {"name": "x"})

    assert prompt == "Hello x"
    assert any("serving English instead" in r.message for r in caplog.records)


def test_a_broken_english_template_propagates(tmp_path: Path) -> None:
    """English has no further fallback: a render failure there is a defect to
    surface, not a gap to paper over."""

    config = _registry(tmp_path, en_template="Hello {{ name.missing_attr }}")
    service = build_prompt_service(config)

    with pytest.raises(UndefinedError):
        service.get_prompt(_PROBE, LanguageTag("en"), {"name": "x"})


# --- boot-time validation ---------------------------------------------------


def test_a_set_but_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_prompt_service(tmp_path / "does-not-exist.yaml")


def test_a_registry_missing_a_component_refuses_to_boot(tmp_path: Path) -> None:
    """Every `PromptIdentifier` the code can ask for must be served, or the
    miss would surface as a KeyError mid-turn instead of a refusal at boot."""

    config = _registry(tmp_path, omit_component="localizer")
    with pytest.raises(ValueError, match="localizer"):
        build_prompt_service(config)


def test_a_component_without_english_refuses_to_boot(tmp_path: Path) -> None:
    config = _registry(tmp_path, include_en=False)
    with pytest.raises(ValueError, match="'en'"):
        build_prompt_service(config)


def test_a_misspelt_registry_key_refuses_to_boot(tmp_path: Path) -> None:
    """`system_prompts:` (plural) would otherwise silently ship a language
    with no template at all."""

    config = _registry(tmp_path)
    broken = config.read_text(encoding="utf-8").replace(
        "system_prompt:", "system_prompts:", 1
    )
    config.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="system_prompt"):
        build_prompt_service(config)


def test_a_template_dropping_a_declared_placeholder_refuses_to_boot(
    tmp_path: Path,
) -> None:
    """The silent-drop bug ``str.format`` had: a typo'd placeholder would ship
    a prompt with no identity in it, and nothing would notice."""

    config = _registry(tmp_path, override_template="नमस्ते {{ nmae }}, {{ name }}")
    with pytest.raises(ValueError, match="nmae"):
        build_prompt_service(config)


def test_a_template_missing_a_declared_placeholder_refuses_to_boot(
    tmp_path: Path,
) -> None:
    config = _registry(tmp_path, override_template="नमस्ते without the name")
    with pytest.raises(ValueError, match="never uses name"):
        build_prompt_service(config)


def test_a_named_template_file_that_is_absent_refuses_to_boot(tmp_path: Path) -> None:
    config = _registry(tmp_path)
    (tmp_path / "prompts" / "hi.j2").unlink()
    with pytest.raises(FileNotFoundError):
        build_prompt_service(config)


def test_a_registry_key_that_is_not_a_tag_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not BCP-47"):
        build_prompt_service(_registry(tmp_path, override_key="gujarati"))


# --- skills -----------------------------------------------------------------


def test_skills_load_with_the_prompt(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path, en_skills=True))
    skills = service.get_skills(_PROBE, LanguageTag("en"))
    assert [s.id for s in skills] == ["test-skill"]


def test_a_language_entry_without_skills_falls_back_to_english_skills(
    tmp_path: Path,
) -> None:
    """An override may replace the prompt alone; the agent still needs tools.
    "Each prompt falls back on its own" holds per file."""

    service = build_prompt_service(_registry(tmp_path, en_skills=True))
    assert service.get_skills(_PROBE, LanguageTag("hi")) == service.get_skills(
        _PROBE, LanguageTag("en")
    )


def test_a_component_without_skills_has_none(tmp_path: Path) -> None:
    service = build_prompt_service(_registry(tmp_path))
    assert service.get_skills(_PROBE, LanguageTag("en")) == ()


# --- category guidance ------------------------------------------------------

_MARKET_GUIDANCE = ("Market", "Name the market and the date.\n")
_WEATHER_GUIDANCE = ("Weather", "Give the rain in millimetres.\n")


def test_category_guidance_is_served_for_the_categories_asked_about(
    tmp_path: Path,
) -> None:
    service = build_prompt_service(
        _registry(
            tmp_path,
            en_skills=True,
            en_category_guidance=(_MARKET_GUIDANCE, _WEATHER_GUIDANCE),
        )
    )

    guidance = service.get_category_guidance(
        _PROBE, LanguageTag("en"), frozenset({SubjectCategory.MARKET})
    )

    assert guidance == "Name the market and the date."


def test_guidance_for_several_categories_comes_in_configured_order(
    tmp_path: Path,
) -> None:
    service = build_prompt_service(
        _registry(
            tmp_path,
            en_skills=True,
            en_category_guidance=(_MARKET_GUIDANCE, _WEATHER_GUIDANCE),
        )
    )

    guidance = service.get_category_guidance(
        _PROBE,
        LanguageTag("en"),
        frozenset({SubjectCategory.WEATHER, SubjectCategory.MARKET}),
    )

    assert guidance == (
        "Name the market and the date.\n\nGive the rain in millimetres."
    )


def test_a_category_with_no_guidance_adds_nothing(tmp_path: Path) -> None:
    service = build_prompt_service(
        _registry(tmp_path, en_skills=True, en_category_guidance=(_MARKET_GUIDANCE,))
    )

    guidance = service.get_category_guidance(
        _PROBE, LanguageTag("en"), frozenset({SubjectCategory.CROP})
    )

    assert guidance == ""


def test_a_language_entry_without_category_guidance_falls_back_to_english(
    tmp_path: Path,
) -> None:
    service = build_prompt_service(
        _registry(tmp_path, en_skills=True, en_category_guidance=(_MARKET_GUIDANCE,))
    )

    guidance = service.get_category_guidance(
        _PROBE, LanguageTag("hi"), frozenset({SubjectCategory.MARKET})
    )

    assert guidance == "Name the market and the date."


def test_guidance_for_a_name_that_is_not_a_category_refuses_to_boot(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Mandi"):
        build_prompt_service(
            _registry(
                tmp_path,
                en_skills=True,
                en_category_guidance=(("Mandi", "Name the market.\n"),),
            )
        )


def test_a_named_guidance_file_that_is_absent_refuses_to_boot(tmp_path: Path) -> None:
    config = _registry(
        tmp_path, en_skills=True, en_category_guidance=(_MARKET_GUIDANCE,)
    )
    (tmp_path / "prompts" / "Market.md").unlink()

    with pytest.raises(FileNotFoundError, match=r"Market\.md"):
        build_prompt_service(config)


def test_a_skill_carries_the_guidance_for_the_categories_asked_about(
    tmp_path: Path,
) -> None:
    service = build_prompt_service(
        _registry(tmp_path, en_skills=True, en_category_guidance=(_MARKET_GUIDANCE,))
    )

    (skill,) = service.get_skills(
        _PROBE, LanguageTag("en"), frozenset({SubjectCategory.MARKET})
    )

    assert "Name the market and the date." in skill.guidance


def test_a_skill_carries_no_guidance_on_a_turn_about_another_category(
    tmp_path: Path,
) -> None:
    service = build_prompt_service(
        _registry(tmp_path, en_skills=True, en_category_guidance=(_MARKET_GUIDANCE,))
    )

    (skill,) = service.get_skills(
        _PROBE, LanguageTag("en"), frozenset({SubjectCategory.CROP})
    )

    assert "Name the market" not in skill.guidance
    assert "{{" not in skill.guidance


def test_a_skill_using_a_value_the_code_does_not_send_refuses_to_boot(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="crop_name"):
        build_prompt_service(
            _registry(tmp_path, en_skills=True, skill_guidance="Grow {{ crop_name }}.")
        )


def test_category_guidance_with_no_place_in_the_prompt_refuses_to_boot(
    tmp_path: Path,
) -> None:
    """Neither the template nor a skill has `category_guidance`, so the
    guidance would never be read."""

    with pytest.raises(ValueError, match="category_guidance"):
        build_prompt_service(
            _registry(tmp_path, en_category_guidance=(_MARKET_GUIDANCE,))
        )
