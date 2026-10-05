"""Tier 2 — the stand-in redactor for when redaction is off, and the factory
that picks between it and the real one."""

from __future__ import annotations

from dss.adapters.redaction.factory import build_redactor
from dss.adapters.redaction.local import LocalRedactor
from dss.adapters.redaction.stub import StubRedactor
from tests.integration.adapters.redaction.test_local import redact_through_port
from tests.support.redaction_rules import CONFIG


async def test_the_stub_changes_nothing() -> None:
    texts = ["mera number 9876543210 hai", "aadhaar 2345 6789 0124"]
    result = await redact_through_port(StubRedactor(), texts)
    assert result.texts == tuple(texts)
    assert result.reveal.values == {}
    assert result.found == {}


async def test_the_stubs_map_leaves_bodies_alone() -> None:
    result = await redact_through_port(StubRedactor(), ["x"])
    assert result.reveal.reveal({"phone": "«phone_1»"}) == {"phone": "«phone_1»"}
    assert result.reveal.conceal("9876543210") == "9876543210"


def test_redaction_off_builds_the_stub() -> None:
    assert isinstance(build_redactor(None), StubRedactor)


def test_redaction_on_builds_the_local_redactor() -> None:
    assert isinstance(build_redactor(CONFIG), LocalRedactor)
