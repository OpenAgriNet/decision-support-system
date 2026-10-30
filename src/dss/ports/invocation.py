"""The seam for the network's provider-invocation hop — implemented by an adapter.

Declared here so the type exists to keep discovery's read-only barrier
enforceable, even before anything calls it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dss.core.forms.models import ActionReply
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.network import NetworkTransactionID


class ProviderCallFailedError(Exception):
    """A provider call did not produce an answer.

    Part of the port, not an adapter detail: the contract is "an answer or
    this", and a caller that wants to keep the turn alive has to be able to
    catch it without importing an adapter.

    Raised only once the adapter's retries are exhausted. ``failure_class``
    says whether retrying could ever have helped — ``TRANSIENT`` for a
    timeout or 429, ``DEFECT`` for a malformed request.
    """

    def __init__(
        self,
        action: str,
        capability: str,
        status_code: int,
        failure_class: FailureClass,
    ) -> None:
        super().__init__(f"{action} failed for {capability}: {status_code}")
        self.capability = capability
        self.status_code = status_code
        self.failure_class = failure_class
        # The provider's own `Retry-After`, when it sent one. Set by the
        # actions that read it.
        self.retry_after_seconds: int | None = None


class SelectFailedError(ProviderCallFailedError):
    """``detail`` is the provider's response body, concealed; it reaches the
    planner's prompt."""

    def __init__(
        self,
        capability: str,
        status_code: int,
        failure_class: FailureClass,
        detail: str | None,
    ) -> None:
        super().__init__("select", capability, status_code, failure_class)
        self.detail = detail


class SupportFailedError(ProviderCallFailedError):
    """No provider text, by design: a failed filing is reported to the
    farmer in the DSS's own words. ``retry_after_seconds`` is the provider's
    `Retry-After`, when it sent one."""

    def __init__(
        self,
        capability: str,
        status_code: int,
        failure_class: FailureClass,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__("support", capability, status_code, failure_class)
        self.retry_after_seconds = retry_after_seconds


class StatusFailedError(ProviderCallFailedError):
    """No provider text, as for a filing: the farmer is told in the DSS's own
    words. ``retry_after_seconds`` is the provider's `Retry-After`, when it
    sent one."""

    def __init__(
        self,
        capability: str,
        status_code: int,
        failure_class: FailureClass,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__("status", capability, status_code, failure_class)
        self.retry_after_seconds = retry_after_seconds


class CapabilityInvocation(Protocol):
    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> Sequence[DiscoveredAnswer]:
        """Return every answer the provider gave, or raise ``SelectFailedError``.

        A sequence, not one answer: one call's resources are grouped by the
        document they came from, and a search provider can draw on several.
        It may be empty — a provider that committed to nothing succeeded at
        saying so.

        ``resource_attributes`` holds tags («phone_1»). The adapter swaps in
        the real values only on the wire, and swaps any value the provider
        echoes back to its tag, so neither its logs nor the answers hold one.
        """
        ...

    async def support(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        """File ``attributes`` with the provider, or raise ``SupportFailedError``.

        ``attributes`` is the whole body the pack's action schema calls for,
        already built. Retried like ``select``: a slow provider may receive
        the same filing twice until the pack gives a duplicate key.
        """
        ...

    async def status(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        """Read the case ``attributes`` name, or raise ``StatusFailedError``.

        ``attributes`` is the whole body the pack's action schema calls for,
        already built. Retried like ``select``: a read is safe to send twice.
        """
        ...
