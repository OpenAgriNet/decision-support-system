"""Tier 2 — building identifiers from the rules file's ``identifiers:`` entries,
and the regex identifier called through the port."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from dss.adapters.pii_identifier.factory import build_identifiers
from dss.adapters.pii_identifier.regex.identifier import RegexIdentifier
from dss.core.redaction.models import PiiSpan
from dss.ports.pii_identifier import IdentifierUnavailable, PiiIdentifier
from tests.support.redaction_rules import SETTINGS


async def identify_through_port(
    identifier: PiiIdentifier, texts: Sequence[str]
) -> list[list[PiiSpan]]:
    """Typed as the port, so a signature drift in an adapter fails here."""

    return await identifier.identify(texts)


async def test_the_regex_identifier_satisfies_the_port() -> None:
    texts = ["mera number 98765 43210 hai", "gehu ka rate?"]
    spans = await identify_through_port(RegexIdentifier(SETTINGS), texts)
    assert [[(s.entity, s.value) for s in found] for found in spans] == [
        [("phone", "9876543210")],
        [],
    ]


def test_a_regex_entry_builds_a_regex_identifier() -> None:
    [identifier] = build_identifiers(
        [
            {
                "type": "regex",
                "rules": [{"entity": "phone", "kind": "pattern", "pattern": r"\d{10}"}],
            }
        ]
    )
    assert isinstance(identifier, RegexIdentifier)
    assert identifier.name == "regex"


def test_an_unknown_type_stops_the_boot() -> None:
    with pytest.raises(IdentifierUnavailable, match="unknown type 'presidio'"):
        build_identifiers([{"type": "presidio"}])


def test_a_bad_rule_stops_the_boot_and_names_it() -> None:
    with pytest.raises(IdentifierUnavailable, match="rule 'phone'"):
        build_identifiers(
            [
                {
                    "type": "regex",
                    "rules": [
                        {"entity": "phone", "kind": "pattern", "pattern": "[6-9"}
                    ],
                }
            ]
        )


def test_a_spacy_entry_builds_a_spacy_identifier() -> None:
    from dss.adapters.pii_identifier.spacy.identifier import SpacyIdentifier

    [identifier] = build_identifiers([{"type": "spacy"}])
    assert isinstance(identifier, SpacyIdentifier)
    assert identifier.name == "spacy"


def test_an_onnx_entry_with_a_missing_folder_stops_the_boot(tmp_path) -> None:
    with pytest.raises(IdentifierUnavailable, match="missing"):
        build_identifiers([{"type": "onnx", "dir": str(tmp_path / "missing")}])


def test_identifiers_are_built_in_file_order() -> None:
    identifiers = build_identifiers(
        [
            {
                "type": "regex",
                "rules": [{"entity": "phone", "kind": "pattern", "pattern": r"\d"}],
            },
            {"type": "spacy"},
        ]
    )
    assert [i.name for i in identifiers] == ["regex", "spacy"]
