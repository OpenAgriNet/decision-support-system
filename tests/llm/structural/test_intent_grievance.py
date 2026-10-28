"""Tier 5 — does a real model label a complaint about a scheme `Grievance`?

Structural only: the category and the interaction are asserted, never the
model's words for the subject. Skipped unless a model key is in `.env` or the
shell, like `test_intent_place_name.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.config.prompt_service import build_prompt_service
from dss.config.settings import Settings
from dss.core.intent.models import InteractionType, SubjectCategory
from dss.core.intent.service import classify_intent
from dss.core.shared.models import TextQuery, UserTurn
from dss.entrypoint.composition import _resolve_model
from dss.observability.stages import Stage
from tests.support.live_model import missing_model_key

_REPO = Path(__file__).parents[3]
_DOTENV = _REPO / ".env"


def _dotenv_values() -> dict[str, str]:
    """`.env`, read without touching `os.environ` (see the place-name file)."""

    return {name: value for name, value in dotenv_values(_DOTENV).items() if value}


_MISSING = missing_model_key(
    Settings().intent_model, {**os.environ, **_dotenv_values()}
)
pytestmark = pytest.mark.skipif(
    _MISSING is not None,
    reason=f"live model test — DSS_INTENT_MODEL needs {_MISSING}, in .env or exported",
)


@pytest.fixture(autouse=True)
def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _dotenv_values().items():
        monkeypatch.setenv(name, value)


def _live_llm() -> PydanticAILLMProvider:
    settings = Settings()
    return PydanticAILLMProvider(
        _resolve_model(settings.intent_model, settings),
        name="intent-classifier",
        stage=Stage.INTENT,
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _turn(query: str) -> UserTurn[TextQuery]:
    return UserTurn[TextQuery](
        input=TextQuery(text=query),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )


async def test_filing_a_scheme_complaint_is_a_grievance_act() -> None:
    prompts = build_prompt_service(_REPO / "configs" / "prompts.yaml")

    classification = await classify_intent(
        _turn("I want to file a PMFBY grievance"), _live_llm(), prompts
    )

    (ask,) = classification.asks
    assert ask.subject_categories is SubjectCategory.GRIEVANCE
    assert ask.interaction_type is InteractionType.ACT
    # The model's own words: the prompt says copy them, so any spelling passes.
    assert "pmfby" in (ask.agriculture_subjects or "").casefold()
    assert ask.place_name is None
    assert ask.place_from_history is False
