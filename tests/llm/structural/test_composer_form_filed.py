"""Tier 5 — does a real composer tell the farmer the ticket, and nothing the
farmer typed? Structural only: the ticket number must appear verbatim and the
phone must not, never a particular wording. Skipped without a model key, like
`test_composer_form_offer.py`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.config.identity_loader import load_identity
from dss.config.prompt_service import build_prompt_service
from dss.config.settings import Settings
from dss.core.forms.models import FieldValue, FormContext, FormSubmission
from dss.core.intent.models import Ask, InteractionType, Scheme, SubjectCategory
from dss.core.planner.models import Result, Source, SourceKind
from dss.core.shared.models import UserTurn
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
_PMFBY_SUPPORT = FormContext(scheme="PMFBY", category="Grievance", action="support")
_PHONE = "9876543210"
_TICKET = "100626000099001"


async def test_the_reply_names_the_ticket_and_never_the_phone() -> None:
    ask = Ask(
        subject_categories=SubjectCategory.GRIEVANCE,
        interaction_type=InteractionType.ACT,
        scheme=_PMFBY,
    )
    portal = Source(
        id="1", name="PMFBY Grievance Portal", kind=SourceKind.PROVIDER, url=None
    )
    filed = {
        "scheme": {"code": "PMFBY"},
        "case": {
            "ticketNo": _TICKET,
            "status": {"code": "Registered"},
            "filedOn": "2026-10-29",
        },
    }
    evidence = build_evidence(
        asks=(ask,),
        sources=(portal,),
        results=(Result(ask_index=0, source_id="1", data=filed),),
    )
    turn = UserTurn[FormSubmission](
        input=FormSubmission(
            form_id="offer-1",
            context=_PMFBY_SUPPORT,
            submission_id="submission-1",
            values=(FieldValue(name="applicantMobile", value=_PHONE),),
        ),
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

    assert _TICKET in text, f"the ticket is missing: {text!r}"
    assert _PHONE not in text, f"the farmer's phone is repeated: {text!r}"
