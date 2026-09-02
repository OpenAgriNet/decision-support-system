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
    those live in the envelope the client adapter builds (plan §3.4).
    """

    capabilities: tuple[str, ...]
    languages: tuple[str, ...]
    coverage: Coverage | None


@dataclass(frozen=True)
class DiscoveryResult:
    """Placeholder — full shape (answers/capabilities/failures/events per plan
    §3.9) waits on DiscoveredAnswer, ProviderCapability, DiscoveryFailure, and
    DiscoveryEvent, none of which are spec'd yet.
    """


@dataclass(frozen=True)
class ProviderCapability:
    """What discovery intends to use, not the provider's full inventory (plan
    §3.10). ``kind`` (plan §3.11) is deferred until host-URL derivation is built.
    """

    provider_id: str
    provider_name: str
    capability: str  # the resource's @type
    resource_id: str  # names which resource `select` commits to
    offer_id: str  # TODO: rename — ecommerce vocabulary, needs an OAN term (plan §6.5)


@dataclass(frozen=True)
class DiscoveredAnswer:
    """Placeholder — fields not yet spec'd in the plan."""
