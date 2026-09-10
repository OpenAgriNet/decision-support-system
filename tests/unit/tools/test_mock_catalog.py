"""Tier 1 — reading a discover request, in the mock network.

The mock stands in for the OAN network so a local run can get a
provider-backed answer. Its one routing decision is which `@type`s the DSS
asked for, which arrives as a jsonpath predicate.

Parsed with a regex, not a jsonpath engine: the mock only needs the `@type`
literals out of a predicate the DSS itself built. Both tests here feed it
`build_discover_request`'s real output, so a change to how the DSS phrases
that predicate fails these rather than silently making the mock answer
nothing.
"""

from __future__ import annotations

from dss.adapters.discovery.client import build_discover_request
from dss.core.provider_discovery.models import ProviderQuery
from tools.mock_network.catalog import requested_types


def _real_request(*capabilities: str, subject_category: str = "Weather") -> dict:
    """A discover body built by the DSS's own builder."""

    return build_discover_request(
        ProviderQuery(
            capabilities=capabilities,
            subject_category=subject_category,
            languages=("en",),
            coverage=None,
        ),
        schema_context_index={
            c: f"https://example.test/{c.split(':')[-1]}/v0.1/context.jsonld"
            for c in capabilities
        },
        message_id="7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
        transaction_id="9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
        timestamp="2026-09-09T10:42:21Z",
    )


def test_one_requested_type_is_read_back() -> None:
    body = _real_request("openagrinet:WeatherObservation")

    assert requested_types(body) == ("openagrinet:WeatherObservation",)


def test_several_requested_types_are_all_read_back() -> None:
    """The DSS names one `schemaContext` per capability when an ask spans several.

    Reading only the first would make the mock answer half a question, and the
    turn would come back partially answered for no reason a log would explain.
    """

    body = _real_request("openagrinet:MandiPrice", "openagrinet:KnowledgeAdvisory")

    assert requested_types(body) == (
        "openagrinet:MandiPrice",
        "openagrinet:KnowledgeAdvisory",
    )


def test_a_request_with_no_schema_context_asks_for_nothing() -> None:
    """A malformed body yields none rather than raising.

    The mock is a dev tool: answering nothing is a `no_match`, which is
    readable. A traceback in the mock's log while the DSS reports a provider
    defect is two puzzles instead of one.
    """

    assert requested_types({"context": {}}) == ()
