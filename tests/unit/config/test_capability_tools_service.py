"""The capability tools service: which Agent Skills a turn is offered, and
the folder layout it refuses to boot without."""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.config.capability_tools_service import build_capability_tools_service
from dss.core.intent.models import SubjectCategory
from dss.core.shared.language import LanguageTag


def _capability_tools(root: Path, language_tag: str, *names: str) -> Path:
    """Write an Agent Skill per name under ``root/<language_tag>/`` and
    return ``root``, laid out like the shipped ``capability_tools/``."""

    for name in names:
        folder = root / language_tag / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The {name} rules.\n---\n",
            encoding="utf-8",
        )
    return root


def test_a_turn_is_offered_the_skills_for_the_categories_asked_about(
    tmp_path: Path,
) -> None:
    root = _capability_tools(tmp_path, "en", "market", "weather")

    offered = build_capability_tools_service(root).get_capability_tools(
        LanguageTag("en"), frozenset({SubjectCategory.MARKET})
    )

    assert offered is not None
    assert offered.names == ("market",)
    assert Path(offered.library_path) == root.resolve() / "en"


def test_a_turn_about_a_category_with_no_skill_is_offered_none(
    tmp_path: Path,
) -> None:
    service = build_capability_tools_service(
        _capability_tools(tmp_path, "en", "market")
    )

    assert (
        service.get_capability_tools(
            LanguageTag("en"), frozenset({SubjectCategory.CROP})
        )
        is None
    )


def test_a_language_is_offered_its_own_skills(tmp_path: Path) -> None:
    root = _capability_tools(tmp_path, "en", "market")
    _capability_tools(root, "hi", "market")

    offered = build_capability_tools_service(root).get_capability_tools(
        LanguageTag("hi-IN"), frozenset({SubjectCategory.MARKET})
    )

    assert offered is not None
    assert Path(offered.library_path) == root.resolve() / "hi"


def test_a_language_without_skills_falls_back_to_english(tmp_path: Path) -> None:
    root = _capability_tools(tmp_path, "en", "market")

    offered = build_capability_tools_service(root).get_capability_tools(
        LanguageTag("gu"), frozenset({SubjectCategory.MARKET})
    )

    assert offered is not None
    assert Path(offered.library_path) == root.resolve() / "en"


def test_a_language_with_no_skills_and_no_english_is_offered_none(
    tmp_path: Path,
) -> None:
    root = _capability_tools(tmp_path, "hi", "market")

    assert (
        build_capability_tools_service(root).get_capability_tools(
            LanguageTag("gu"), frozenset({SubjectCategory.MARKET})
        )
        is None
    )


def test_a_skill_named_for_no_category_refuses_to_boot(tmp_path: Path) -> None:
    """Intent never asks for it, so it would never be offered."""

    with pytest.raises(ValueError, match="mandi"):
        build_capability_tools_service(_capability_tools(tmp_path, "en", "mandi"))


def test_a_folder_not_named_for_a_language_refuses_to_boot(tmp_path: Path) -> None:
    """Skills put straight under the root, with no language folder."""

    with pytest.raises(ValueError, match="language tag"):
        build_capability_tools_service(_capability_tools(tmp_path, "market", "market"))


def test_an_absent_folder_refuses_to_boot(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="capability tools dir"):
        build_capability_tools_service(tmp_path / "missing")
