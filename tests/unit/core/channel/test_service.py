"""Tier 1 — the deterministic channel shaping.

The prose-writing composer lives in `orchestration/compose.py` (it needs an
LLM); what stays here is the fixed no-match reply. `answer_from_evidence` has
its own test alongside this one.
"""

from __future__ import annotations

from dss.core.channel.service import (
    NEEDS_DISTRICT_TEXT,
    NO_MATCH_TEXT,
    needs_district_answer,
    no_match_answer,
)


def test_no_match_answer_has_one_block_and_no_sources() -> None:
    answer = no_match_answer()

    assert len(answer.content) == 1
    assert answer.content[0].text == NO_MATCH_TEXT
    # nothing was consulted, so nothing is cited
    assert answer.sources == ()
    assert answer.content[0].source_ids == ()


def test_needs_district_answer_asks_for_a_district() -> None:
    """Deterministic like the no-match reply — asking a fixed question needs no
    model. It asks for a *district* specifically: the lookup holds only
    districts, so "where are you from?" would invite a village and fail again.
    """

    answer = needs_district_answer()

    assert len(answer.content) == 1
    assert answer.content[0].text == NEEDS_DISTRICT_TEXT
    assert "district" in NEEDS_DISTRICT_TEXT.lower()
    assert answer.sources == ()
    assert answer.content[0].source_ids == ()
