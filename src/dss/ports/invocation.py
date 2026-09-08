"""The seam for the network's provider-invocation hop — implemented by an adapter.

Declared here so the type exists to keep discovery's read-only barrier
enforceable, even before anything calls it.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)


class SelectFailed(Exception):
    """A ``select`` call did not produce an answer.

    Part of the port, not an adapter detail: the contract is "an answer or
    this", and a caller that wants to keep the turn alive has to be able to
    catch it without importing an adapter.

    Raised only once the adapter's retries are exhausted. ``failure_class``
    says whether retrying could ever have helped — ``TRANSIENT`` for a
    timeout or 429, ``DEFECT`` for a malformed request.
    """

    def __init__(
        self,
        capability: str,
        status_code: int,
        failure_class: FailureClass,
        detail: str | None,
    ) -> None:
        super().__init__(f"select failed for {capability}: {status_code} ({detail})")
        self.capability = capability
        self.status_code = status_code
        self.failure_class = failure_class
        self.detail = detail


class CapabilityInvocation(Protocol):
    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: str,
    ) -> DiscoveredAnswer:
        """Return the provider's answer, or raise ``SelectFailed``."""
        ...
