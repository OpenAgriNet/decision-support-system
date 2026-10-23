"""Contract tests for RegistryProviderPreferences — the per-turn network hop.

Uses httpx.MockTransport so no real socket is ever opened, per tier 2's
'recorded fixture or local test server, never live network calls' rule. The
stub below answers the registry's search API by sender id, which is also the
shape the e2e stub serves.
"""

from __future__ import annotations

import json

import httpx

from dss.adapters.provider_preferences.registry import RegistryProviderPreferences

BASE_URL = "https://registry.oan.dev"
SEARCH_PATH = "/api/v1/SenderPreference/search"

DEFAULT_ROW = {
    "senderId": "default",
    "status": "active",
    "preferences": [
        {"capability": "Weather", "providers": ["mausamgram"]},
        {"capability": "Market", "providers": ["agmarknet"]},
    ],
}

MH_ROW = {
    "senderId": "mh-consumer",
    "status": "active",
    "preferences": [
        {"capability": "Weather", "providers": ["maharashtra-vistaar", "mausamgram"]},
    ],
}


class _StubRegistry:
    """Answers the search API from a fixed set of rows, like the registry does:
    the `or` filter matches any of the listed ids (Sunbird RC FilterOperators),
    and an unknown id is simply not in the result, never an error."""

    def __init__(self, *rows: dict, status_code: int = 200) -> None:
        self._rows = {row["senderId"]: row for row in rows}
        self._status_code = status_code
        self.searches: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == SEARCH_PATH
        asked = json.loads(request.content)["filters"]["senderId"]["or"]
        self.searches.append(asked)
        found = [self._rows[one] for one in asked if one in self._rows]
        return httpx.Response(
            self._status_code, json={"totalCount": len(found), "data": found}
        )


def _preferences(handler) -> RegistryProviderPreferences:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RegistryProviderPreferences(
        client=client, base_url=BASE_URL, timeout_seconds=1.0
    )


async def _ask(handler, sender_id: str | None):
    return await _preferences(handler).for_sender(sender_id, transaction_id="txn-1")


async def test_a_registered_sender_is_served_its_own_ranking() -> None:
    preference = await _ask(_StubRegistry(DEFAULT_ROW, MH_ROW), "mh-consumer")

    assert preference.known is True
    assert preference.ranking_for("Weather") == ("maharashtra-vistaar", "mausamgram")


async def test_a_capability_the_sender_did_not_rank_falls_back_to_the_default() -> None:
    """The merge is per capability, and it is `PreferenceBook`'s — defined in
    core, not re-derived here."""

    preference = await _ask(_StubRegistry(DEFAULT_ROW, MH_ROW), "mh-consumer")

    assert preference.ranking_for("Market") == ("agmarknet",)


async def test_one_search_fetches_both_rows() -> None:
    """The `or` filter carries the senderId and `default` together, so the
    whole lookup is one network call and an unregistered sender costs no
    second round trip."""

    registry = _StubRegistry(DEFAULT_ROW, MH_ROW)
    await _ask(registry, "mh-consumer")

    assert registry.searches == [["mh-consumer", "default"]]


async def test_an_unregistered_sender_is_served_the_default() -> None:
    preference = await _ask(_StubRegistry(DEFAULT_ROW), "nobody-registered-this")

    assert preference.known is False
    assert preference.ranking_for("Weather") == ("mausamgram",)


async def test_a_turn_without_a_sender_asks_only_for_the_default() -> None:
    registry = _StubRegistry(DEFAULT_ROW)
    preference = await _ask(registry, None)

    assert registry.searches == [["default"]]
    assert preference.ranking_for("Market") == ("agmarknet",)


async def test_a_registry_error_costs_the_ranking_never_the_turn() -> None:
    """A 500 on the search reads as "no preference anywhere" — the
    behaviour before this feature — not as an exception into the turn."""

    preference = await _ask(_StubRegistry(status_code=500), "mh-consumer")

    assert preference.known is False
    assert preference.ranking_for("Weather") == ()


async def test_an_unparseable_response_reads_as_no_row() -> None:
    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    preference = await _ask(garbage, "mh-consumer")

    assert preference.ranking_for("Weather") == ()


async def test_an_inactive_row_is_ignored() -> None:
    retired = dict(MH_ROW, status="inactive")
    preference = await _ask(_StubRegistry(DEFAULT_ROW, retired), "mh-consumer")

    assert preference.known is False
    assert preference.ranking_for("Weather") == ("mausamgram",)


async def test_a_malformed_entry_is_skipped_not_fatal() -> None:
    """The registry is edited while the DSS runs. One bad entry must not sink
    the row's other capabilities — a turn has nowhere to refuse to boot."""

    row = {
        "senderId": "mh-consumer",
        "status": "active",
        "preferences": [
            {"capability": "Weather"},  # no providers
            {"capability": "   ", "providers": ["x"]},  # no capability
            {"capability": "Market", "providers": ["agmarknet", "  "]},
        ],
    }
    preference = await _ask(_StubRegistry(row), "mh-consumer")

    assert preference.ranking_for("Weather") == ()
    assert preference.ranking_for("Market") == ("agmarknet",)


async def test_an_inactive_entry_is_ignored() -> None:
    row = {
        "senderId": "mh-consumer",
        "status": "active",
        "preferences": [
            {"capability": "Weather", "providers": ["maharashtra-vistaar"]},
            {"capability": "Market", "providers": ["agmarknet"], "status": "inactive"},
        ],
    }
    preference = await _ask(_StubRegistry(DEFAULT_ROW, row), "mh-consumer")

    assert preference.ranking_for("Weather") == ("maharashtra-vistaar",)
    # The retired entry falls back to the default layer, like an unranked one.
    assert preference.source_for("Market") == "default"
