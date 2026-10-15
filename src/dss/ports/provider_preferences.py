"""The seam for a client's preferred providers — implemented by an adapter.

**Async on purpose, over an implementation that needs nothing of the sort.**
The rankings are a mounted YAML file today, read once at boot and answered
from memory (ADR-0019). The planned next step is a preference service the
DSS asks at turn time, so the one caller already awaits this and swapping in
an HTTP adapter is a change in the composition root and nowhere else. A
synchronous port would have made that swap a change to every caller instead.

The port answers for *one client*, already resolved against the default
ranking — not "give me the whole book". A preference service will hold far
more clients than any one deployment serves, and a port that could only hand
over everything would have no sensible implementation against it.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_preferences.models import ClientPreference


class ProviderPreferences(Protocol):
    async def for_client(self, client_id: str | None) -> ClientPreference:
        """Which providers this client wants, per topic, best first.

        `client_id` is `None` when the caller sent no `X-Client-Id` header,
        and an arbitrary string when it sent one nobody configured. Both get
        the default ranking: an unrecognised caller is answered, never
        refused, because refusing would make every experience layer's
        rollout of the header a breaking change.

        Never raises for an unknown client. A configuration this cannot read
        is a boot-time failure, not a turn-time one.
        """
        ...
