"""Tier 1 — the network envelope, as types rather than string literals.

The wire must not move. These assert the exact dicts the two adapters used to
build by hand, so a rename or a reordering that changed the bytes would fail
here before it reached a provider.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dss.adapters.network import (
    DiscoverContext,
    NetworkAction,
    NetworkSchemaContext,
    NetworkVersion,
    SelectContext,
)


def test_the_actions_are_the_two_this_network_uses():
    # Both answer synchronously, so there is no `on_` reply message to name.
    assert {action.value for action in NetworkAction} == {"discover", "select"}


def test_there_is_one_protocol_version():
    # Discovery and invocation each held their own copy of this string.
    assert NetworkVersion.V2_0_0 == "2.0.0"


def test_a_discover_context_is_the_dict_the_adapter_used_to_build():
    context = DiscoverContext(
        message_id="m-1", transaction_id="t-1", timestamp="2026-01-01T00:00:00Z"
    )

    assert context.to_wire() == {
        "action": "discover",
        "version": "2.0.0",
        "messageId": "m-1",
        "transactionId": "t-1",
        "timestamp": "2026-01-01T00:00:00Z",
    }


def test_a_discover_omits_schema_context_rather_than_sending_an_empty_one():
    """#52. The contract takes either the filter or `schemaContext`, and `[]`
    would assert that no schema applies rather than that none was named."""

    context = DiscoverContext(
        message_id="m-1", transaction_id="t-1", timestamp="ts", schema_context=None
    )

    assert "schemaContext" not in context.to_wire()


def test_a_discover_carries_the_context_urls_as_a_plain_list():
    schema_context = NetworkSchemaContext.for_types(
        ("openagrinet:MandiPrice",),
        {
            "openagrinet:MandiPrice": "https://example.test/MandiPrice/v0.1/context.jsonld"
        },
    )
    context = DiscoverContext(
        message_id="m-1",
        transaction_id="t-1",
        timestamp="ts",
        schema_context=schema_context,
    )

    assert context.to_wire()["schemaContext"] == [
        "https://example.test/MandiPrice/v0.1/context.jsonld#openagrinet:MandiPrice"
    ]


def test_no_types_named_means_no_schema_context_at_all():
    assert NetworkSchemaContext.for_types((), {}) is None


def test_a_select_context_is_the_dict_the_adapter_used_to_build():
    context = SelectContext(
        sender_id="consumer.oan.dev",
        receiver_id="oan",
        message_id="m-2",
        transaction_id="t-1",
        timestamp="2026-01-01T00:00:00Z",
    )

    assert context.to_wire() == {
        "action": "select",
        "version": "2.0.0",
        "senderId": "consumer.oan.dev",
        "receiverId": "oan",
        "messageId": "m-2",
        "transactionId": "t-1",
        "timestamp": "2026-01-01T00:00:00Z",
    }


def test_a_select_cannot_carry_a_discover_field():
    # The two envelopes are not the same shape, and modelling them as one
    # nullable type would let a select ask about a schema.
    with pytest.raises(ValidationError):
        SelectContext(
            sender_id="a",
            receiver_id="b",
            message_id="m",
            transaction_id="t",
            timestamp="ts",
            schema_context=None,
        )


def test_a_discover_cannot_carry_a_select_field():
    with pytest.raises(ValidationError):
        DiscoverContext(
            message_id="m", transaction_id="t", timestamp="ts", sender_id="a"
        )


def test_the_action_is_not_something_a_caller_can_change():
    with pytest.raises(ValidationError):
        DiscoverContext(
            action=NetworkAction.SELECT,
            message_id="m",
            transaction_id="t",
            timestamp="ts",
        )
