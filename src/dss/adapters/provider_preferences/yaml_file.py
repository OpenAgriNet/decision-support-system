"""Reads client provider rankings from a mounted YAML file (ADR-0019).

The file is deployment configuration, not domain data an agriculture officer
owns — it names network provider ids, which is why it is YAML like the policy
pack rather than CSV like the scheme catalog.

Nothing ships in the image and nothing is mounted by default. Unset means no
ranking applies and every ask keeps the candidates discovery found, which is
the behaviour that existed before this file did. That default is deliberate:
a ranking is exclusive, so a shipped file naming providers a deployment does
not actually talk to would silently drop every candidate and read as "nobody
serves this". `config/examples/provider-preferences.yaml` is a ready file to
copy and edit.

    version: 1
    default:
      Weather: [bharat-vistaar, maharashtra-vistaar]
    clients:
      kisan-mitra-web:
        Weather: [maharashtra-vistaar, bharat-vistaar]

A malformed file raises at boot, never at turn time: a deployment's typo
should stop a deployment, not quietly change which provider answers a farmer.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dss.core.intent.models import SubjectCategory
from dss.core.provider_preferences.models import (
    ClientPreference,
    PreferenceBook,
    ProviderId,
    topic_key,
)

logger = logging.getLogger(__name__)

# A ready file to copy. Nothing reads it unless a deployment points
# DSS_PROVIDER_PREFERENCES_PATH at it — the same stance as the redaction rules.
EXAMPLE_PREFERENCES = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "examples"
    / "provider-preferences.yaml"
)

# The only version this reader understands. Present so a later shape change
# can be rejected by name rather than by a confusing validation error.
SUPPORTED_VERSION = 1

UNSET_PREFERENCES = (
    "DSS_PROVIDER_PREFERENCES_PATH is not set, so no client provider ranking "
    "is loaded: every ask keeps whichever providers discovery found, in "
    "whatever order it found them, for every client. Mount a preferences file "
    "to have a client's vetted providers answer first."
)

# Every topic a ranking may name. Closed, because `SubjectCategory` is — and a
# typo ("Wether") would otherwise load cleanly and then never match an ask,
# which looks exactly like a ranking that was never written.
_TOPICS = {topic_key(category.value): category.value for category in SubjectCategory}


class MalformedProviderPreferencesError(Exception):
    """The preferences file will not load. Raised at boot."""


class _Preferences(BaseModel):
    """The file's shape. Validation only — the merge rule is core's."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = SUPPORTED_VERSION
    default: dict[str, list[str]] = Field(default_factory=dict)
    clients: dict[str, dict[str, list[str]]] = Field(default_factory=dict)

    @field_validator("version")
    @classmethod
    def _supported(cls, version: int) -> int:
        if version != SUPPORTED_VERSION:
            raise ValueError(
                f"version {version} is not supported — this build reads "
                f"version {SUPPORTED_VERSION}"
            )
        return version


class YamlProviderPreferences:
    """A `ProviderPreferences` over a book read once at boot.

    `for_client` is async because the port is (see `ports/provider_preferences.py`);
    nothing here awaits anything.
    """

    def __init__(self, book: PreferenceBook) -> None:
        self._book = book

    async def for_client(self, client_id: str | None) -> ClientPreference:
        return self._book.for_client(client_id)


def load_provider_preferences(path: Path | None) -> YamlProviderPreferences:
    """Read and validate the rankings.

    - `path` unset → an empty book and one warning; no ranking ever applies.
    - `path` set but missing → `FileNotFoundError`.
    - `path` set but broken → `MalformedProviderPreferencesError`, naming what.
    """

    if path is None:
        logger.warning(UNSET_PREFERENCES)
        return YamlProviderPreferences(PreferenceBook(default={}, clients={}))

    if not path.exists():
        raise FileNotFoundError(
            f"DSS_PROVIDER_PREFERENCES_PATH is set to {path} but no file is "
            f"there — refusing to boot on a ranking that cannot be read"
        )

    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise MalformedProviderPreferencesError(
            f"{path.name} is not valid YAML: {exc}"
        ) from exc

    if not isinstance(data, Mapping):
        raise MalformedProviderPreferencesError(
            f"{path.name} must be a mapping with 'default' and/or 'clients' keys"
        )

    try:
        parsed = _Preferences.model_validate(data)
    except ValueError as exc:
        raise MalformedProviderPreferencesError(f"{path.name}: {exc}") from exc

    book = PreferenceBook(
        default=_rankings(parsed.default, source="default", file=path.name),
        clients={
            client: _rankings(rankings, source=f"clients.{client}", file=path.name)
            for client, rankings in _named_clients(parsed.clients, file=path.name)
        },
    )
    logger.info(
        "provider rankings loaded from %s: %d client(s), %d default topic(s)",
        path.name,
        len(book.clients),
        len(book.default),
    )
    return YamlProviderPreferences(book)


def _named_clients(
    clients: dict[str, dict[str, list[str]]], *, file: str
) -> list[tuple[str, dict[str, list[str]]]]:
    """Every client entry, refusing a blank id.

    A blank id can never match a header value, so it is a typo that would sit
    in the file looking like configuration and doing nothing.
    """

    named = []
    for client, rankings in clients.items():
        if not client.strip():
            raise MalformedProviderPreferencesError(
                f"{file}: a client entry has a blank id — it could never match "
                f"an X-Client-Id header"
            )
        named.append((client.strip(), rankings))
    return named


def _rankings(
    raw: dict[str, list[str]], *, source: str, file: str
) -> dict[str, tuple[ProviderId, ...]]:
    """One layer's topic → ordered provider ids, keyed for lookup.

    Every topic is checked against the closed subject taxonomy, and every
    list must name at least one provider: an empty list reads as "prefer
    nothing", which — a ranking being exclusive — would drop every provider
    for that topic and answer nothing. If that is what a deployment wants, it
    is a change nobody makes by leaving brackets empty.
    """

    rankings: dict[str, tuple[ProviderId, ...]] = {}
    for topic, providers in raw.items():
        key = topic_key(topic)
        if key not in _TOPICS:
            raise MalformedProviderPreferencesError(
                f"{file}: {source} names the topic {topic!r}, which is not one "
                f"of {', '.join(sorted(_TOPICS.values()))}"
            )
        if key in rankings:
            raise MalformedProviderPreferencesError(
                f"{file}: {source} ranks the topic {topic!r} twice"
            )
        rankings[key] = _ordered_providers(
            providers, topic=topic, source=source, file=file
        )
    return rankings


def _ordered_providers(
    providers: list[str], *, topic: str, source: str, file: str
) -> tuple[ProviderId, ...]:
    cleaned = [provider.strip() for provider in providers]
    if not cleaned or any(not provider for provider in cleaned):
        raise MalformedProviderPreferencesError(
            f"{file}: {source}.{topic} must list at least one provider id, and "
            f"no blank ones — a ranking is the whole answer for its topic, so "
            f"an empty one would leave the ask with no provider at all"
        )
    duplicate = next(
        (p for i, p in enumerate(cleaned) if p in cleaned[:i]),
        None,
    )
    if duplicate is not None:
        raise MalformedProviderPreferencesError(
            f"{file}: {source}.{topic} lists {duplicate!r} twice — a provider "
            f"has one position in a ranking"
        )
    return tuple(cleaned)
