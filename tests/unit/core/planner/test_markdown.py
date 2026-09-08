"""Tier 1 — rendering a DiscoveredAnswer as markdown for the model.

A flat key:value bullet renderer for now — no schema-derived field labels
yet (deferred to a follow-up chunk). Wrapped in markers so a provider's text
is read as retrieved data, never as instructions.
"""

from __future__ import annotations

from dss.core.planner.markdown import render_answer_as_markdown
from dss.core.provider_discovery.models import DiscoveredAnswer


def _answer(attributes: dict) -> DiscoveredAnswer:
    return DiscoveredAnswer(
        provider_id="agmarknet",
        provider_name="Agmarknet",
        capability="openagrinet:MandiPrice",
        resource_id="res:agmarknet:daily-price",
        attributes=attributes,
        validity=None,
    )


def test_wraps_the_answer_in_markers() -> None:
    markdown = render_answer_as_markdown(_answer({"commodity": {"code": "PADDY"}}))
    assert "<BEGIN RETRIEVED DATA>" in markdown
    assert "<END RETRIEVED DATA>" in markdown


def test_renders_flat_fields_as_bullets() -> None:
    markdown = render_answer_as_markdown(_answer({"arrivalDate": "2026-08-25"}))
    assert "- arrivalDate: 2026-08-25" in markdown


def test_renders_nested_fields_indented() -> None:
    markdown = render_answer_as_markdown(
        _answer({"prices": {"modal": 2200, "unit": "INR/quintal"}})
    )
    assert "- prices:" in markdown
    assert "  - modal: 2200" in markdown
    assert "  - unit: INR/quintal" in markdown


def test_renders_a_list_of_scalars() -> None:
    markdown = render_answer_as_markdown(_answer({"subjectCategories": ["Market"]}))
    assert "- subjectCategories: Market" in markdown
