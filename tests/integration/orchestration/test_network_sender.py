"""Tier 3 — who the DSS says it is on the network, per turn."""

from __future__ import annotations

from dss.orchestration.network_sender import (
    caller_or_configured_sender,
    configured_sender,
)


def test_toggle_off_ignores_the_callers_id(a_turn) -> None:
    sender_for = configured_sender("consumer.oan.dev")

    assert sender_for(a_turn(sender_id="kisan-mitra-web")) == "consumer.oan.dev"


def test_toggle_on_uses_the_callers_id(a_turn) -> None:
    sender_for = caller_or_configured_sender("consumer.oan.dev")

    assert sender_for(a_turn(sender_id="kisan-mitra-web")) == "kisan-mitra-web"


def test_toggle_on_without_caller_id_uses_dss_id(a_turn) -> None:
    """The HTTP mapping turns a missing or blank `senderId` into `None`."""

    sender_for = caller_or_configured_sender("consumer.oan.dev")

    assert sender_for(a_turn(sender_id=None)) == "consumer.oan.dev"
