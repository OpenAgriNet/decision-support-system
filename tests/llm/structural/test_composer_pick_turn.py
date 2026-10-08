"""Tier 5 — does the composer still answer when the farmer's words are "2"?

On a pick turn the farmer replies "2" to "Which Rampur?". The composer is
handed that reply as the question, beside the weather data for the picked
Rampur. Its prompt says to say it could not find the answer when the data does
not answer the question, so a strict model may refuse.

Structural only: the answer must cite the source. A refusal cites nothing.

Skipped unless `.env` carries a model key, like `test_intent_place_name.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.config.identity_loader import load_identity
from dss.config.prompt_service import load_prompt_service
from dss.config.settings import Settings
from dss.core.intent.models import (
    Ask,
    Intent,
    InteractionType,
    PlaceSource,
    ResolvedPlace,
    SubjectCategory,
)
from dss.core.planner.models import Evidence, Result, Source, SourceKind
from dss.core.shared.models import ConversationMessage, Geometry, UserTurn
from dss.core.stream_response.service import stream_response
from dss.entrypoint.composition import _composer_llm
from tests.support.live_model import missing_model_key

_DOTENV = Path(__file__).parents[3] / ".env"


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


async def test_pick_reply_still_answers() -> None:
    """The farmer picked line 2. The answer must be the weather, not "I could
    not find anything for 2"."""

    rampur = ResolvedPlace(
        name="Rampur",
        within=("India", "Himachal Pradesh"),
        geometry=Geometry(coordinates=[77.63, 31.45]),
        source=PlaceSource.NAMED,
    )
    intent = Intent(
        asks=(
            Ask(
                subject_categories=SubjectCategory.WEATHER,
                interaction_type=InteractionType.OBSERVE,
                place=rampur,
            ),
        )
    )
    evidence = Evidence(
        sources=(Source(id="1", name="IMD", kind=SourceKind.PROVIDER, url=None),),
        results=(
            Result(
                ask_index=0,
                source_id="1",
                data={"forecast": "light rain", "rainfall_mm": 37},
            ),
        ),
        served=(0,),
        failed=(),
        sufficient=True,
    )
    turn = UserTurn(
        original_query="2",
        enriched_query="2",
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=[
            ConversationMessage(role="user", text="What is the weather in Rampur?"),
            ConversationMessage(
                role="assistant",
                text="Which Rampur?\n1. Rampur, Uttar Pradesh\n"
                "2. Rampur, Himachal Pradesh",
            ),
        ],
    )

    pieces = stream_response(
        evidence,
        intent,
        turn=turn,
        identity=load_identity(),
        llm=_composer_llm(Settings()),
        prompts=load_prompt_service(
            Path(__file__).parents[3] / "configs" / "prompts.yaml"
        ),
    )
    text = "".join([piece async for piece in pieces])

    assert "[1]" in text, f"no citation, so no answer from the data: {text!r}"
