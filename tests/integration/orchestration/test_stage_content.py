"""Tier 3 — the composer span names each source by both numbers."""

from __future__ import annotations

from dss.core.planner.models import Evidence, Source, SourceKind
from dss.orchestration.stage_content import cited_sources_text
from tests.support.evidence import build_evidence


def _evidence(*names: str) -> Evidence:
    return build_evidence(
        sources=tuple(
            Source(id=str(i), name=name, kind=SourceKind.PROVIDER, url=None)
            for i, name in enumerate(names, start=1)
        ),
    )


def test_each_source_shows_the_number_the_farmer_read() -> None:
    text = cited_sources_text(_evidence("PoP", "KAU Mango Guide"), {"2": "1"})

    assert text == "[1] PoP → not cited\n[2] KAU Mango Guide → [1]"


def test_no_sources_says_so() -> None:
    assert cited_sources_text(_evidence(), {}) == "no sources"
