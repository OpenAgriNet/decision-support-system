"""Tier 3 — the shipped example rules, end to end: load the file, build its
identifiers, run them side by side, redact.

The example file ships patterns only. The first test runs it on the card's
romanised Hinglish examples. The second adds a spaCy entry to it, as an adopter
would, and runs both on English text, where spaCy works. spaCy is off by default
because it tags "gehu" as a person — the gap ADR-0016 records.
"""

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


async def test_the_example_rules_with_spacy_redact_names_and_numbers() -> None:
    config = load_redaction_config(enabled=True, path=EXAMPLE_RULES)
    assert config is not None
    identifiers = build_identifiers(
        [*config.identifiers, {"type": "spacy", "entity": "person"}]
    )
    assert [i.name for i in identifiers] == ["regex", "spacy"]

    result = await redact_texts(
        [
            # Announced name (regex and spaCy both find it) and an Aadhaar.
            "My name is Ramesh Patil and my Aadhaar is 2345 6789 0124.",
            # A name only spaCy finds, and a card number.
            "Please ask Sunita Devi to pay with card 4111 1111 1111 1111.",
            # A name only spaCy finds, and a phone written with a gap.
            "Priya Sharma asked about the PM-KISAN instalment, call 98765 43210.",
            # The same person again: same tag as before.
            "Sunita Devi wants to know the onion price in Nashik.",
        ],
        identifiers,
        config.policy,
    )

    assert result.texts == (
        "My name is «person_1» and my Aadhaar is «aadhaar_1».",
        "Please ask «person_2» to pay with card «card_1».",
        "«person_3» asked about the PM-KISAN instalment, call «phone_1».",
        "«person_2» wants to know the onion price in Nashik.",
    )
    # Names and the phone are kept for a provider; Aadhaar and card never are.
    assert result.reveal.values == {
        "«person_1»": "Ramesh Patil",
        "«person_2»": "Sunita Devi",
        "«person_3»": "Priya Sharma",
        "«phone_1»": "9876543210",
    }
    assert result.found == {"person": 4, "aadhaar": 1, "card": 1, "phone": 1}
    assert result.failed == ()
