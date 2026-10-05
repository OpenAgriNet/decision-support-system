"""Tier 2 — the spaCy identifier, with the real ``en_core_web_sm`` model.

The model is a local package, so no network is touched.
"""

from __future__ import annotations

import pytest

from dss.adapters.pii_identifier.spacy.identifier import SpacyIdentifier
from dss.adapters.pii_identifier.spacy.models import SpacySettings
from tests.integration.adapters.pii_identifier.test_factory import (
    identify_through_port,
)


@pytest.fixture(scope="module")
def identifier() -> SpacyIdentifier:
    return SpacyIdentifier(SpacySettings())


async def test_an_english_name_is_found(identifier: SpacyIdentifier) -> None:
    text = "My name is John Smith and I farm near Pune."
    [span] = await identify_through_port(identifier, text)
    assert text[span.start : span.end] == "John Smith"
    assert (span.entity, span.value, span.source) == ("person", "John Smith", "spacy")


async def test_a_place_is_not_a_name(identifier: SpacyIdentifier) -> None:
    text = "What is the onion price in Pune today?"
    assert await identify_through_port(identifier, text) == []


async def test_an_empty_text_finds_nothing(identifier: SpacyIdentifier) -> None:
    assert await identify_through_port(identifier, "") == []


async def test_the_entity_label_is_configurable() -> None:
    identifier = SpacyIdentifier(SpacySettings(entity="name"))
    [span] = await identifier.identify("My name is John Smith.")
    assert span.entity == "name"
