"""Tier 1 — the settlement types."""

from __future__ import annotations

import pytest

from dss.core.settlement.models import (
    Ambiguous,
    AskSlot,
    Frame,
    Missing,
    Resolved,
    Slot,
)


def test_one_choice_needs_confirmation() -> None:
    assert Ambiguous("Pune", ("Pune",)).needs_confirmation
    assert not Ambiguous("Rampur", ("Rampur, UP", "Rampur, HP")).needs_confirmation


def test_frame_refuses_repeated_slot() -> None:
    """Two states for one slot leave nothing to say which one counts."""

    place = AskSlot.PLACE
    with pytest.raises(ValueError, match="place"):
        Frame(slots=(Slot(place, Missing()), Slot(place, Resolved("Pune"))))
