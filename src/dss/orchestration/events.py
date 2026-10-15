"""The events the core of a turn yields, shared by every layer above it.

`TurnStarted` is not one of them: it belongs to the edge, which sends it
before redaction or translation, so the farmer's app hears back at once.
"""

from __future__ import annotations

from dss.core.shared.models import Claim, ClaimDelta, TurnFinished

type CoreEvent = ClaimDelta | Claim | TurnFinished
