"""Types produced and consumed by provider_discovery.

Data shapes only. Behaviour lives in service.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


@dataclass(frozen=True)
class Coverage:
    lat: float
    lon: float
    radius_m: int  # caller-supplied — config today, may vary per ask later


@dataclass(frozen=True)
class ProviderQuery:
    """A domain-level discovery query. No ask index, no trace id, no timestamp —
    those live in the envelope the client adapter builds.
    """

    capabilities: tuple[str, ...]
    languages: tuple[str, ...]
    coverage: Coverage | None


@dataclass(frozen=True)
class ProviderCapability:
    """What discovery intends to use, not the provider's full inventory.

    ``kind`` is deferred until host-URL derivation is built.
    """

    provider_id: str
    provider_name: str
    capability: str  # the resource's @type
    resource_id: str  # names which resource `select` commits to
    observed_categories: tuple[str, ...] = ()  # the resource's own subjectCategories


@dataclass(frozen=True)
class Validity:
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True)
class DiscoveredAnswer:
    """A Direct resource's answer. ``attributes`` is the pack-specific
    ``resourceAttributes`` verbatim — provider_discovery doesn't know every
    pack's schema, so it passes them through rather than typing each one.
    """

    provider_id: str
    provider_name: str
    capability: str
    resource_id: str
    attributes: dict[str, object]
    validity: Validity | None


@dataclass(frozen=True)
class CapabilityUnresolved:
    """The (subject_category, action_type) pair resolves to zero @type values.

    Every valid pair should map to at least one @type, so this means our own
    mapping is incomplete — not that no provider serves this pair.
    """

    subject_category: str
    action_type: str


class FailureClass(Enum):
    TRANSIENT = "transient"
    DEFECT = "defect"


@dataclass(frozen=True)
class DiscoveryFailure:
    """A discovery call failed for one capability.

    ``provider_id`` is None for a whole-call failure — the only kind seen in
    a real response so far. A federated discovery service could plausibly
    report a per-provider partial failure inside one response; this field is
    ready for that but unverified against real data.
    """

    capability: str
    status_code: int
    failure_class: FailureClass
    provider_id: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ExpiredAnswerDropped:
    """A Direct answer was outside its validity window and was dropped."""

    provider_id: str
    capability: str
    resource_id: str
    had_fallback: bool  # an OnDemand capability existed on the same provider+capability


@dataclass(frozen=True)
class AskDiscoveryFailed:
    """A discovery call failed for one ask — mirrors the DiscoveryFailure."""

    ask_index: int
    capability: str
    failure_class: FailureClass
    status_code: int


@dataclass(frozen=True)
class AskUnservable:
    """An ask ended with no answer and no capability — the normal no_match path."""

    ask_index: int
    capabilities: tuple[str, ...]  # the @type values the query carried, for context


@dataclass(frozen=True)
class CategoryMappingDiverged:
    """A returned resource's own subjectCategories contradicts the index."""

    capability: str
    observed_category: str


DiscoveryEvent = (
    CapabilityUnresolved
    | ExpiredAnswerDropped
    | AskDiscoveryFailed
    | AskUnservable
    | CategoryMappingDiverged
)


@dataclass(frozen=True)
class DiscoveryResult:
    answers: dict[int, tuple[DiscoveredAnswer, ...]]
    capabilities: dict[int, tuple[ProviderCapability, ...]]
    failures: dict[int, tuple[DiscoveryFailure, ...]]
    events: tuple[DiscoveryEvent, ...]


@dataclass(frozen=True)
class SchemaPackFiles:
    """Raw file content for one schema pack — before any parsing.

    Only the three files the index needs. ``vocab.jsonld``, ``renderer.json``,
    and ``context.jsonld`` are never fetched.
    """

    pack_name: str
    version: str
    profile_json: str
    attributes_yaml: str
    examples_json: tuple[str, ...]
