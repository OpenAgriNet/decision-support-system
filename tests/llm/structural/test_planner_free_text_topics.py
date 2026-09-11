"""Tier 5 — does a real model write ``topics`` from the farmer, or copy the
provider's catalog?

The fix this pins is prompt-shaped: ``describe_capability`` stops rendering a
field the provider advertised under its own filterable path, and the
``provider-invocation`` skill tells the model that a field with no listed
values is free text it must compose. Neither half can be shown to work by a
unit test — only a real model reading the real shipped prompt can.

Structural only, per the tier's rule. The assertions are that ``topics`` is a
non-empty list of strings and that no entry is one of the labels this provider
published — a shape-and-provenance claim. The exact phrase ("Potato in Pune")
is never asserted anywhere: that is tier 4's job, behind a cassette.

Two turns, because carrying the crop forward from an earlier message is the
half of the behaviour a single-turn query cannot exercise.

Skipped unless `.env` carries a model key, like `test_intent_place_name.py`:
the suite stays green on a checkout with no credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values

from dss.config.identity_loader import load_identity
from dss.config.settings import Settings
from dss.config.skill_loader import load_skills
from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.moderation.models import ModerationDecision, Outcome
from dss.core.planner.models import Verdict
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.shared.models import ConversationMessage, UserTurn
from dss.entrypoint.composition import _resolve_model
from dss.orchestration.plan import build_plan

_DOTENV = Path(__file__).parents[3] / ".env"


def _dotenv_values() -> dict[str, str]:
    """`.env`, read *without* touching `os.environ` — see the note in
    `test_intent_place_name.py`."""

    return {name: value for name, value in dotenv_values(_DOTENV).items() if value}


pytestmark = pytest.mark.skipif(
    not (_dotenv_values().keys() & {"OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"}),
    reason="live model test — needs OPENAI_API_KEY or AZURE_OPENAI_API_KEY in .env",
)


@pytest.fixture(autouse=True)
def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _dotenv_values().items():
        monkeypatch.setenv(name, value)


# What the provider published. The real KnowledgeAdvisory catalog advertises
# `topics` under the pack's own filterable path — these are the strings the
# model used to send back verbatim.
PUBLISHED_TOPICS = ("Crop establishment", "Nutrient management", "Pest management")

CAPABILITY = ProviderCapability(
    provider_id="krishi-kb",
    provider_name="Krishi Knowledge Base",
    capability="openagrinet:KnowledgeAdvisory",
    resource_id="res:krishi-kb:crop-advisory",
    observed_categories=("Crop",),
    advertised={
        "topics": list(PUBLISHED_TOPICS),
        "agricultureSubjects": [{"code": "POTATO", "name": "Potato"}],
    },
)

# The pack's real filterable paths, so `topics` is both settable and the name
# the provider advertised under — which is the whole case under test.
SCHEMAS = {
    "openagrinet:KnowledgeAdvisory": DomainSchema(
        type="KnowledgeAdvisory",
        filterable=("topics", "agricultureSubjects[].subjectId", "languages"),
    )
}

ADVICE_ASK = Ask(
    agriculture_subjects="potato",
    subject_categories=SubjectCategory.CROP,
    interaction_type=InteractionType.ADVISE,
)


class _RecordingInvocation:
    """Records what the model asked to send, and answers so the loop ends."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer:
        self.calls.append(resource_attributes)
        return DiscoveredAnswer(
            provider_id=capability.provider_id,
            provider_name=capability.provider_name,
            capability=capability.capability,
            resource_id="res:krishi-kb:crop-advisory:2026-09-11",
            attributes={
                "recommendations": [
                    {"message": "Plant certified seed tubers in well-drained soil."}
                ]
            },
            validity=None,
        )


def _turn() -> UserTurn:
    """The second turn of a two-turn conversation. The crop is only in the
    history; the place is only in the current message."""

    return UserTurn(
        original_query="i want to grow in pune",
        enriched_query="i want to grow in pune",
        transaction_id="txn-topics",
        session_id="s-topics",
        source_lang="en",
        target_lang="en",
        channel="web",
        history=(ConversationMessage(role="user", text="can i grow potato"),),
    )


def _cleared_verdict() -> Verdict:
    verdict = Verdict()
    verdict.set(ModerationDecision(outcome=Outcome.PROCEED))
    return verdict


@pytest.fixture
async def sent_topics() -> list[str]:
    """Run the real planner against a live model; return the `topics` it sent."""

    settings = Settings()
    invocation = _RecordingInvocation()
    plan = build_plan(
        schemas=SCHEMAS,
        schema_context_index={
            "openagrinet:KnowledgeAdvisory": (
                "https://openagrinet.github.io/network-specs/schema/"
                "KnowledgeAdvisory/v0.1/context.jsonld"
            )
        },
        invocation=invocation,
        # The shipped identity and skills, not doubles: the guidance under test
        # is exactly what a deployment loads.
        identity=load_identity(),
        skills=load_skills(),
        model=_resolve_model(settings.planner_model),
        temperature=settings.planner_temperature,
        timeout_seconds=settings.planner_timeout_seconds,
        retries=settings.planner_retries,
    )

    await plan(
        _turn(),
        intent=Intent(asks=(ADVICE_ASK,), confidence=0.9, place_name="Pune"),
        discovery=DiscoveryResult(
            answers={0: ()},
            capabilities={0: (CAPABILITY,)},
            failures={0: ()},
            events=(),
        ),
        verdict=_cleared_verdict(),
    )

    assert invocation.calls, "the model called no provider"
    topics = invocation.calls[0].get("topics")
    assert isinstance(topics, list) and topics, f"no topics sent: {invocation.calls[0]}"
    assert all(isinstance(entry, str) for entry in topics), topics
    return topics


async def test_no_topic_is_a_label_the_provider_published(
    sent_topics: list[str],
) -> None:
    """The defect, stated as an assertion: the model used to answer "can i grow
    potato" by sending this provider's own ``Crop establishment`` back to it.

    Provenance, not wording — which is what this tier may assert.
    """

    copied = [entry for entry in sent_topics if entry in PUBLISHED_TOPICS]
    assert not copied, f"copied the provider's own labels: {copied}"


async def test_the_topic_names_the_crop_and_the_place(
    sent_topics: list[str],
) -> None:
    """Both words have to be there, and each comes from a different message —
    the crop from the history, the place from the current turn. A model that
    reads only the last message writes about Pune and not about potato.

    Substring containment, not an exact string: "Potato in Pune", "potato
    cultivation in Pune" and "growing potato in Pune district" all pass, and
    all are correct.
    """

    written = " ".join(sent_topics).casefold()
    assert "potato" in written, f"lost the crop from the earlier message: {sent_topics}"
    assert "pune" in written, f"lost the place from this message: {sent_topics}"
