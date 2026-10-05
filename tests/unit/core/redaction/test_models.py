"""Tier 1 — the entity policy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.core.redaction.models import RedactionPolicy, ValueHandling


def test_keep_and_destroy() -> None:
    policy = RedactionPolicy(
        entities={"phone": ValueHandling.KEEP, "aadhaar": ValueHandling.DESTROY}
    )
    assert policy.keeps("phone")
    assert not policy.keeps("aadhaar")


def test_an_unlisted_entity_is_not_kept() -> None:
    policy = RedactionPolicy(entities={"phone": ValueHandling.KEEP})
    assert not policy.keeps("person")


def test_entity_names_must_fit_in_a_tag() -> None:
    with pytest.raises(ValidationError):
        RedactionPolicy(entities={"Phone Number": ValueHandling.KEEP})


def test_a_policy_needs_an_entity() -> None:
    with pytest.raises(ValidationError):
        RedactionPolicy(entities={})


def test_parses_from_plain_data() -> None:
    policy = RedactionPolicy.model_validate({"entities": {"phone": "keep"}})
    assert policy.keeps("phone")
