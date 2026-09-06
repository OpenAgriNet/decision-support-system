"""Tier 1 — planner prompt building.

No framework, no network. Asserts on prompt *content*, not exact wording —
each test checks that a specific fact from the input is present in the
rendered string, matching this repo's "don't assert exact LLM-facing text"
convention outside tier 4.

Only Direct answers (``DiscoveryResult.answers``) go in the prompt: the
catalog already has those values, no tool call needed. OnDemand candidates
(``DiscoveryResult.capabilities``) do NOT belong here — the model reaches
those through the ``select`` tool's own ``RunContext.deps``, wired in
``orchestration/planner.py`` (tier 3), not through prompt text.
"""

from __future__ import annotations

from dss.core.planner.models import Identity, Skill
from dss.core.planner.prompt import build_planner_prompt, build_user_message
from dss.core.provider_discovery.models import DiscoveredAnswer
from dss.core.shared.models import ConversationMessage


def _identity() -> Identity:
    return Identity(
        name="Kisan Mitra",
        persona="A calm, practical farm advisor.",
        boundaries="Never gives financial or legal advice.",
    )


def _skill() -> Skill:
    return Skill(
        id="provider-invocation",
        domain="agriculture",
        description="Call providers to answer an ask.",
        guidance="Read the capability, build resourceAttributes, then call select.",
        tool_names=("select",),
    )


def test_system_prompt_includes_identity() -> None:
    prompt = build_planner_prompt(identity=_identity(), skills=(_skill(),), answers={})
    assert "Kisan Mitra" in prompt
    assert "Never gives financial or legal advice." in prompt


def test_system_prompt_includes_skill_guidance() -> None:
    prompt = build_planner_prompt(identity=_identity(), skills=(_skill(),), answers={})
    assert "Read the capability, build resourceAttributes, then call select." in prompt


def test_system_prompt_includes_direct_answers() -> None:
    answer = DiscoveredAnswer(
        provider_id="krishi-kb",
        provider_name="Krishi KB",
        capability="openagrinet:KnowledgeAdvisory",
        resource_id="res:krishi-kb:crop-advisory",
        attributes={"soilType": "sandy loam"},
        validity=None,
    )
    prompt = build_planner_prompt(
        identity=_identity(), skills=(_skill(),), answers={0: (answer,)}
    )
    assert "Krishi KB" in prompt
    assert "sandy loam" in prompt


def test_user_message_wraps_query_in_markers() -> None:
    message = build_user_message(query="price of potato", history=())
    assert "<BEGIN CONVERSATION>" in message
    assert "<END CONVERSATION>" in message
    assert "price of potato" in message


def test_user_message_includes_history() -> None:
    history = (ConversationMessage(role="user", text="what about wheat?"),)
    message = build_user_message(query="and potato?", history=history)
    assert "what about wheat?" in message
    assert "and potato?" in message
