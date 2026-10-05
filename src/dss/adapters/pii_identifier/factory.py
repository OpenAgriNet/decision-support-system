"""Builds the identifiers the rules file lists, at boot.

Each ``identifiers:`` entry names its ``type``; the matching adapter checks the
rest of the entry against its own settings. An unknown type or a bad entry
raises ``IdentifierUnavailable`` naming it, so the boot stops with a reason.
A new identifier — an HTTP PII service, say — is one more entry in ``_BUILDERS``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from dss.ports.pii_identifier import IdentifierUnavailable, PiiIdentifier


def _regex(entry: Mapping[str, Any]) -> PiiIdentifier:
    from dss.adapters.pii_identifier.regex.identifier import RegexIdentifier
    from dss.adapters.pii_identifier.regex.models import RegexSettings

    return RegexIdentifier(RegexSettings.model_validate(entry))


def _spacy(entry: Mapping[str, Any]) -> PiiIdentifier:
    from dss.adapters.pii_identifier.spacy.identifier import SpacyIdentifier
    from dss.adapters.pii_identifier.spacy.models import SpacySettings

    return SpacyIdentifier(SpacySettings.model_validate(entry))


_BUILDERS: dict[str, Callable[[Mapping[str, Any]], PiiIdentifier]] = {
    "regex": _regex,
    "spacy": _spacy,
}


def build_identifiers(entries: Sequence[Mapping[str, Any]]) -> list[PiiIdentifier]:
    identifiers: list[PiiIdentifier] = []
    for i, entry in enumerate(entries):
        kind = entry.get("type")
        builder = _BUILDERS.get(str(kind))
        if builder is None:
            raise IdentifierUnavailable(
                f"identifiers[{i}]: unknown type '{kind}' "
                f"(known: {', '.join(sorted(_BUILDERS))})"
            )
        try:
            identifiers.append(builder(entry))
        except ValidationError as exc:
            raise IdentifierUnavailable(f"identifiers[{i}] ({kind}): {exc}") from exc
    return identifiers
