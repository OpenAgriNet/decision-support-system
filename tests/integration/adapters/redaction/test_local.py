"""Tier 2 — the in-process redactor, called through the port."""

from __future__ import annotations

from collections.abc import Sequence

from dss.adapters.redaction.local import LocalRedactor
from dss.core.redaction.service import Redaction
from dss.ports.redactor import Redactor
from tests.support.redaction_rules import CONFIG


async def redact_through_port(redactor: Redactor, texts: Sequence[str]) -> Redaction:
    """Typed as the port, so a signature drift in the adapter fails here."""

    return await redactor.redact(texts)


async def test_it_redacts_a_question_and_its_history() -> None:
    result = await redact_through_port(
        LocalRedactor(CONFIG),
        ["mera number 98765 43210 hai", "aadhaar 2345 6789 0124"],
    )
    assert result.texts == ("mera number «phone_1» hai", "aadhaar «aadhaar_1»")
    assert result.reveal.values == {"«phone_1»": "9876543210"}
    assert result.found == {"phone": 1, "aadhaar": 1}


async def test_nothing_to_redact_comes_back_unchanged() -> None:
    result = await redact_through_port(LocalRedactor(CONFIG), ["gehu ka rate?"])
    assert result.texts == ("gehu ka rate?",)
    assert result.found == {}
