"""Tier 3 — the shipped example rules, end to end: load the file, build its
identifiers, redact the card's examples."""

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
        build_identifiers(config.identifiers),
        config.policy,
    )
    assert result.texts == (
        "mera aadhaar «aadhaar_1» hai, gehu ka rate kya hai?",
        "mera number «phone_1» hai, meri application ka status?",
        "champa ka rate kya hai?",
    )
    assert result.reveal.values == {"«phone_1»": "9876543210"}


async def test_the_example_rules_redact_agri_identifiers() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    result = await redact_texts(
        [
            "meri PM Kisan registration UP123456789 hai, kist kab aayegi?",
            "farmer id 12345678901 aur farm id MH123456789012 hai",
            # Eleven digits that are a phone with its leading 0: phone wins.
            "call 09876543210",
        ],
        build_identifiers(config.identifiers),
        config.policy,
    )
    assert result.texts == (
        "meri PM Kisan registration «pm_kisan_id_1» hai, kist kab aayegi?",
        "farmer id «farmer_id_1» aur farm id «farm_id_1» hai",
        "call «phone_1»",
    )
    # All three are kept for the provider that needs them.
    assert result.reveal.values == {
        "«pm_kisan_id_1»": "UP123456789",
        "«farmer_id_1»": "12345678901",
        "«farm_id_1»": "MH123456789012",
        "«phone_1»": "09876543210",
    }
