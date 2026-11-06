"""Tier 5 — does a real model fall back with ``select_next`` when the first
ranked provider's data cannot answer the ask, and keep it when it can?

The choice is the model's, guided by the shipped ``provider-invocation``
skill, so only a real model reading the real prompt can show it. The
assertion is provenance only: which provider the ask's evidence came from.

Skipped unless `.env` carries a model key, like `test_intent_place_name.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.config.identity_loader import load_identity
from dss.config.prompt_service import build_prompt_service
from dss.config.settings import Settings
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.provider_preferences.models import AskPriority
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import TextQuery, UserTurn
from dss.entrypoint.composition import _resolve_model
from dss.orchestration.plan import build_plan
from tests.support.evidence import attempted
from tests.support.live_model import missing_model_key
from tests.support.providers import build_capability

_REPO_ROOT = Path(__file__).parents[3]


def _dotenv_values() -> dict[str, str]:
    """`.env`, read *without* touching `os.environ` — see the note in
    `test_intent_place_name.py`."""

    return {
        name: value
        for name, value in dotenv_values(_REPO_ROOT / ".env").items()
        if value
    }


_MISSING = missing_model_key(
    Settings().planner_model, {**os.environ, **_dotenv_values()}
)
pytestmark = pytest.mark.skipif(
    _MISSING is not None,
    reason=f"live model test — DSS_PLANNER_MODEL needs {_MISSING}, in .env or exported",
)


@pytest.fixture(autouse=True)
def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _dotenv_values().items():
        monkeypatch.setenv(name, value)


ADVISORY = "openagrinet:KnowledgeAdvisory"
SCHEMAS = {
    ADVISORY: DomainSchema(
        type="KnowledgeAdvisory",
        filterable=("topics", "agricultureSubjects[].subjectId", "languages"),
    )
}
SCHEMA_CONTEXT_INDEX = {
    ADVISORY: (
        "https://openagrinet.github.io/network-specs/schema/"
        "KnowledgeAdvisory/v0.1/context.jsonld"
    )
}

PREFERRED = "krishi-kb"
NEXT_PREFERRED = "agro-advisory"

POTATO_BLIGHT_ADVICE = (
    "For late blight in potato, spray mancozeb at 2.5 g per litre at the "
    "first sign of dark lesions on the leaves, and repeat every 7 to 10 days "
    "in wet weather."
)
WHEAT_SOWING_ADVICE = (
    "Sow wheat between 1 and 25 November at 100 kg seed per hectare, with "
    "rows 20 cm apart."
)

BLIGHT_ASK = Ask(
    agriculture_subjects="potato",
    subject_categories=SubjectCategory.CROP,
    interaction_type=InteractionType.ADVISE,
)


def _capability(provider_id: str) -> ProviderCapability:
    return build_capability(
        provider_id=provider_id,
        provider_name=provider_id,
        capability=ADVISORY,
        resource_id=f"res:{provider_id}:crop-advisory",
        observed_categories=("Crop",),
    )


def _advice(provider_id: str, message: str) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id=provider_id,
        provider_name=provider_id,
        capability=ADVISORY,
        resource_id=f"res:{provider_id}:crop-advisory:today",
        attributes={"recommendations": [{"message": message}]},
        validity=None,
    )


class _ScriptedInvocation:
    """Answers each provider with the advice it was given."""

    def __init__(self, advice_by_provider: dict[str, str]) -> None:
        self._advice_by_provider = advice_by_provider

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> list[DiscoveredAnswer]:
        message = self._advice_by_provider[capability.provider_id]
        return [_advice(capability.provider_id, message)]


def _ranked_discovery() -> DiscoveryResult:
    ranking = (PREFERRED, NEXT_PREFERRED)
    return DiscoveryResult(
        answers={0: ()},
        capabilities={0: tuple(_capability(provider) for provider in ranking)},
        failures={0: ()},
        events=(),
        priorities={
            0: AskPriority(
                ask_index=0,
                capability="Crop",
                sender_id="kisan-mitra-web",
                source="sender",
                ranking=ranking,
                kept=ranking,
                dropped=(),
            )
        },
    )


def _turn() -> UserTurn:
    return UserTurn(
        input=TextQuery(text="how do i control late blight in my potato crop"),
        transaction_id="txn-select-next",
        session_id="s-select-next",
        source_lang="en",
        target_lang="en",
        channel="web",
        sender_id="kisan-mitra-web",
    )


def _cleared_verdict() -> Verdict:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return verdict


async def _find_answering_providers(
    *, preferred_advice: str, next_preferred_advice: str
) -> set[str]:
    """Run the real planner on a live model; return who the ask's evidence
    came from."""

    settings = Settings()
    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index=SCHEMA_CONTEXT_INDEX,
        invocation=_ScriptedInvocation(
            {PREFERRED: preferred_advice, NEXT_PREFERRED: next_preferred_advice}
        ),
        identity=load_identity(),
        prompts=build_prompt_service(_REPO_ROOT / "configs" / "prompts.yaml"),
        model=_resolve_model(settings.planner_model, settings),
        temperature=settings.planner_temperature,
        timeout_seconds=settings.planner_timeout_seconds,
        retries=settings.planner_retries,
    )

    evidence = await plan(
        _turn(),
        intent=Intent(asks=(BLIGHT_ASK,), confidence=0.9),
        discovery=_ranked_discovery(),
        verdict=_cleared_verdict(),
    )

    source_names = {source.id: source.name for source in evidence.sources}
    (blight_evidence,) = evidence.ask_evidences
    assert blight_evidence.is_served, "the model left the ask unanswered"
    return {
        source_names[result.source_id] for result in attempted(blight_evidence).results
    }


async def test_off_topic_data_falls_back_to_the_next_provider() -> None:
    answering = await _find_answering_providers(
        preferred_advice=WHEAT_SOWING_ADVICE,
        next_preferred_advice=POTATO_BLIGHT_ADVICE,
    )

    assert answering == {NEXT_PREFERRED}


async def test_data_that_answers_the_ask_is_kept() -> None:
    answering = await _find_answering_providers(
        preferred_advice=POTATO_BLIGHT_ADVICE,
        next_preferred_advice=WHEAT_SOWING_ADVICE,
    )

    assert answering == {PREFERRED}
