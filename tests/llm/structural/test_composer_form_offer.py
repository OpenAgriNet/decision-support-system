"""Tier 5 — does a real composer point the farmer at the form, and nothing
more? Structural only: the reply must mention the form and must not name a
field, never a particular wording. Skipped without a model key, like
`test_composer_pick_turn.py`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.config.identity_loader import load_identity
from dss.config.prompt_service import build_prompt_service
from dss.config.settings import Settings
from dss.core.forms.models import (
    FieldDefinition,
    FieldKind,
    FieldRules,
    FormContext,
    FormDefinition,
)
from dss.core.intent.models import Ask, InteractionType, Scheme, SubjectCategory
from dss.core.shared.models import TextQuery, UserTurn
from dss.core.stream_response.service import stream_response
from dss.entrypoint.composition import _composer_llm
from tests.support.evidence import build_evidence
from tests.support.live_model import missing_model_key

_REPO = Path(__file__).parents[3]
_DOTENV = _REPO / ".env"


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


_PMFBY = Scheme(code="PMFBY", name="Pradhan Mantri Fasal Bima Yojana")
_FIELD_LABELS = ("Enrolment id", "Applicant mobile", "Crop year", "Season")
_GRIEVANCE_FORM = FormDefinition(
    context=FormContext(scheme="PMFBY", category="Grievance", action="support"),
    fields=tuple(
        FieldDefinition(
            name=label.lower().replace(" ", ""),
            label=label,
            kind=FieldKind.TEXT,
            rules=FieldRules(required=True),
        )
        for label in _FIELD_LABELS
    ),
)


async def test_the_reply_points_at_the_form_and_names_no_field() -> None:
    ask = Ask(
        agriculture_subjects=_PMFBY.name,
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=_PMFBY,
    )
    evidence = build_evidence(asks=(ask,), awaiting={0: _GRIEVANCE_FORM})
    turn = UserTurn[TextQuery](
        input=TextQuery(text="I want to file a PMFBY grievance"),
        session_id="s1",
        transaction_id="t1",
        source_lang="en",
        target_lang="en",
        channel="web",
    )

    pieces = stream_response(
        evidence,
        turn=turn,
        identity=load_identity(),
        llm=_composer_llm(Settings()),
        prompts=build_prompt_service(_REPO / "configs" / "prompts.yaml"),
    )
    text = "".join([piece async for piece in pieces])

    assert "form" in text.casefold(), f"no mention of the form: {text!r}"
    named = [label for label in _FIELD_LABELS if label.casefold() in text.casefold()]
    assert not named, f"the reply lists fields the form carries: {named} in {text!r}"
