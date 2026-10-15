from __future__ import annotations

from dss.core.shared.models import Claim, ClaimDelta, TurnFinished

# Not `TurnStarted`: the edge sends it before redaction or translation.
type CoreEvent = ClaimDelta | Claim | TurnFinished
