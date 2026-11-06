"""Tier 1 — the settlement types."""

from __future__ import annotations

import pytest

from dss.core.settlement.models import Filled, Frame, Missing, Slot, SlotName

PLACE = SlotName("place")


def test_frame_refuses_repeated_slot() -> None:
    """Two states for one slot leave nothing to say which one counts."""

    with pytest.raises(ValueError, match="place"):
        Frame(slots=(Slot(PLACE, Missing()), Slot(PLACE, Filled("Pune"))))
