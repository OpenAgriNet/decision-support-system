"""Types produced and consumed by provider_discovery.

Data shapes only. Behaviour lives in service.py.
"""

from __future__ import annotations

from dataclasses import dataclass


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
class DiscoveryResult:
    """Placeholder — full shape (answers/capabilities/failures/events) waits
    on DiscoveredAnswer, ProviderCapability, DiscoveryFailure, and
    DiscoveryEvent, none of which are spec'd yet.
    """


@dataclass(frozen=True)
class ProviderCapability:
    """What discovery intends to use, not the provider's full inventory.
    ``kind`` is deferred until host-URL derivation is built.
    """

    provider_id: str
    provider_name: str
    capability: str  # the resource's @type
    resource_id: str  # names which resource `select` commits to
    offer_id: str  # TODO: rename — ecommerce vocabulary, needs an OAN term


@dataclass(frozen=True)
class DiscoveredAnswer:
    """Placeholder — fields not yet spec'd in the plan."""


@dataclass(frozen=True)
class CapabilityUnresolved:
    """The (subject_category, action_type) pair resolves to zero @type values.

    Every valid pair should map to at least one @type, so this means our own
    mapping is incomplete — not that no provider serves this pair.
    """

    subject_category: str
    action_type: str


@dataclass(frozen=True)
class SchemaPackFiles:
    """Raw file content for one schema pack — before any parsing.

    Only the three files the index needs. ``vocab.jsonld``, ``renderer.json``,
    and ``context.jsonld`` are never fetched.
    """

    pack_name: str
    profile_json: str
    attributes_yaml: str
    examples_json: tuple[str, ...]
