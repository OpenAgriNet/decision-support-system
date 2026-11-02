"""Who the DSS says it is on the network for a turn.

One rule for the whole turn, so a discover and the selects that follow it
never name different senders.
"""

from __future__ import annotations

from collections.abc import Callable

from dss.core.shared.models import UserTurn

type NetworkSenderFor = Callable[[UserTurn], str]
