"""The seam for the network's provider-invocation hop — implemented by an adapter.

Declared here so the type exists to keep discovery's read-only barrier
enforceable, even before anything calls it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from dss.core.forms.models import ActionReply
from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import ProviderFailureKind
from dss.core.shared.network import NetworkTransactionID

type ProviderEndpoint = Literal["select", "init", "support", "status"]


class ProviderCallFailedError(Exception):
    """A provider call did not produce an answer.

    Part of the port, not an adapter detail: the contract is "an answer or
    this", and a caller that wants to keep the turn alive has to be able to
    catch it without importing an adapter.

    Raised only once the adapter's retries are exhausted. ``reason`` is the
    provider's reply body, masked; ``None`` when it sent none.
    """

    def __init__(
        self,
        endpoint: ProviderEndpoint,
        capability: str,
        kind: ProviderFailureKind,
        *,
        reason: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(f"{endpoint} failed for {capability}: {kind}")
        self.endpoint = endpoint
        self.capability = capability
        self.kind = kind
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


class ProviderActionInvocation(Protocol):
    # Sent once, never retried: each try can text the farmer a new OTP.
    async def init(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply: ...

    async def support(
        self,
        capability: ProviderCapability,
        attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> ActionReply:
        """File ``attributes`` with the provider, or raise ``ProviderCallFailedError``.

        ``attributes`` is the whole body the pack's action schema calls for,
        already built. Retried only when the provider surely did not take it
        in: there is no duplicate key yet.
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
        """Read the case ``attributes`` name, or raise ``ProviderCallFailedError``.

        ``attributes`` is the whole body the pack's action schema calls for,
        already built. Retried like ``select``: a read is safe to send twice.
        """
        ...


class CapabilityInvocation(ProviderActionInvocation, Protocol):
    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> Sequence[DiscoveredAnswer]:
        """Return every answer the provider gave, or raise ``ProviderCallFailedError``.

        A sequence, not one answer: one call's resources are grouped by the
        document they came from, and a search provider can draw on several.
        It may be empty — a provider that committed to nothing succeeded at
        saying so.

        ``resource_attributes`` holds tags («phone_1»). The adapter swaps in
        the real values only on the wire, and swaps any value the provider
        echoes back to its tag, so neither its logs nor the answers hold one.
        """
        ...
