"""Tier 5 — does a real model keep tags, markers, numbers and scheme names
through translation? Presence only, never wording.

Runs on `DSS_COMPOSER_MODEL`; skips without its key, like
`test_composer_pick_turn.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.config.prompt_service import build_prompt_service
from dss.config.settings import Settings
from dss.core.shared.language import ENGLISH, LanguageTag
from dss.entrypoint.composition import _build_translation_routes
from dss.ports.translator import Translator
from tests.support.live_model import missing_model_key

_ROOT = Path(__file__).parents[3]
_DOTENV = _ROOT / ".env"

GUJARATI = LanguageTag("gu")
TEXT = "PM-KISAN હપ્તો ₹2000 «phone_1» પર [1] 15 કિલો"
KEPT = ("2000", "«phone_1»", "[1]", "15", "PM-KISAN")


def _dotenv_values() -> dict[str, str]:
    """`.env`, read without touching `os.environ` (see the intent tier 5 file)."""

    return {name: value for name, value in dotenv_values(_DOTENV).items() if value}


_MISSING = missing_model_key(
    Settings().composer_model, {**os.environ, **_dotenv_values()}
)
pytestmark = pytest.mark.skipif(
    _MISSING is not None,
    reason=f"live model test — DSS_COMPOSER_MODEL needs {_MISSING}, in .env",
)


@pytest.fixture(autouse=True)
def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _dotenv_values().items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def translator(tmp_path: Path) -> Translator:
    config = tmp_path / "edge-translation.yaml"
    config.write_text(
        "languages:\n"
        f"  gu: {{to: en, adapter: llm, model: '{Settings().composer_model}'}}\n",
        encoding="utf-8",
    )
    routes = _build_translation_routes(
        Settings(stub_llm=False, edge_translation_config_path=config),
        prompts=build_prompt_service(_ROOT / "configs" / "prompts.yaml"),
    )
    return routes["gu"].translator


def _find_lost_pieces(text: str) -> list[str]:
    return [kept for kept in KEPT if kept not in text]


async def test_gu_round_trip_keeps_numbers_tags_markers_and_scheme_names(
    translator: Translator,
) -> None:
    # The way the layer uses it: a batch in, a stream out.
    (english,) = await translator.translate([TEXT], from_lang=GUJARATI, to_lang=ENGLISH)
    back = "".join(
        [
            piece
            async for piece in translator.stream(
                english, from_lang=ENGLISH, to_lang=GUJARATI
            )
        ]
    )
    print(f"\n  gu → en: {english!r}\n  en → gu: {back!r}")

    assert not _find_lost_pieces(english), (
        f"gu → en lost {_find_lost_pieces(english)}: {english!r}"
    )
    assert not _find_lost_pieces(back), (
        f"en → gu lost {_find_lost_pieces(back)}: {back!r}"
    )


async def test_a_batch_comes_back_one_text_per_text(translator: Translator) -> None:
    """The service rejects a reply of the wrong length, which fails the turn."""

    texts = ["આજે વરસાદ પડશે?", "નમસ્તે", "મારા ઘઉંનો ભાવ શું છે?"]

    translated = await translator.translate(texts, from_lang=GUJARATI, to_lang=ENGLISH)

    assert len(translated) == len(texts)
    assert all(text.strip() for text in translated)
