"""Tier 3 — the shipped example rules, end to end: load the file, build its
regex identifier, redact the card's examples.

Only the regex entry: the spaCy entry tags "gehu" as a person, which is the
known gap ADR-0016 records, not behaviour to pin here."""

from __future__ import annotations

from dss.adapters.pii_identifier.factory import build_identifiers
from dss.config.redaction_loader import EXAMPLE_RULES, load_redaction_config
from dss.orchestration.redaction import redact_texts


async def test_the_example_rules_redact_the_cards_examples() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    result = await redact_texts(
        [
            "mera aadhaar 2345 6789 0124 hai, gehu ka rate kya hai?",
            "mera number 98765 43210 hai, meri application ka status?",
            "champa ka rate kya hai?",
        ],
        build_identifiers([e for e in config.identifiers if e["type"] == "regex"]),
        config.policy,
    )
    assert result.texts == (
        "mera aadhaar «aadhaar_1» hai, gehu ka rate kya hai?",
        "mera number «phone_1» hai, meri application ka status?",
        "champa ka rate kya hai?",
    )
    assert result.reveal.values == {"«phone_1»": "9876543210"}
