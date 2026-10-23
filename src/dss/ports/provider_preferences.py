"""The seam for a sender's preferred providers — implemented by an adapter.

Implemented by the network registry adapter, asked on every turn
(ADR-0020) — the DSS holds no rankings of its own. The composition root's
no-registry stand-in answers "no preference" for every sender. The port is
async so the two are interchangeable in the composition root and nowhere
else.

The port answers for *one sender*, already resolved against the default
ranking — not "give me the whole book". The registry holds far more senders
than any one deployment serves, and a port that could only hand over
everything would have no sensible implementation against it.
"""

from __future__ import annotations

from typing import Protocol

from dss.core.provider_preferences.models import SenderPreference


class ProviderPreferences(Protocol):
    async def for_sender(
        self, sender_id: str | None, *, transaction_id: str
    ) -> SenderPreference:
        """Which providers this sender wants, per capability, best first.

        `sender_id` is `None` when the request carried no `senderId`, and an
        arbitrary string when it carried one nobody registered. Both get the
        default ranking: an unrecognised caller is answered, never refused,
        because refusing would make every experience layer's rollout of the
        field a breaking change.

        `transaction_id` is the turn's own, for the adapter's request and
        response log lines (ADR-0014) — an implementation that makes no call
        ignores it.

        Never raises. A registry that cannot be reached at turn time is
        answered as "no row found", never as a failed turn.
        """
        ...
