"""Reads a sender's provider ranking from the network registry (ADR-0020).

The registry is asked on every turn, the way `/discover` and `/select` are
asked — the rankings are the registry's row data, maintained there and not
mounted into the DSS. One search fetches both rows the turn needs: the
sender's and the reserved id `default`, matched with the registry's `or`
filter operator (its IN), so a sender nobody registered still lands on the
default ladder without a second call. The per-capability merge of the two
stays in `core.provider_preferences` (`PreferenceBook`), so no reader of
these rows can drift on what "falls back to the default" means.

**A ranking lookup never fails a turn.** A search that errors, times out, or
comes back unparseable counts as "no rows found" and is logged as a warning;
the turn then runs as if no preference existed — the behaviour before this
feature.

The wire shape mirrors the registry's `SenderPreference` entity. `or` and
`eq` are Sunbird RC `FilterOperators`; `or` matches any of the listed values:

    POST {base_url}/api/v1/SenderPreference/search
    {"filters": {"senderId": {"or": ["mh-consumer", "default"]}}}

    {"totalCount": 2, "data": [
        {"senderId": "default", "status": "active",
         "preferences": [{"capability": "Market", "providers": ["agmarknet"]}]},
        {"senderId": "mh-consumer", "status": "active",
         "preferences": [{"capability": "Weather",
                          "providers": ["mausamgram", "bharat-vistaar"]}]}]}

`_ENTITY` and `_ID_FIELD` below are the whole coupling to those names — if
the registry publishes the entity differently, they are the only edit.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from dss.core.intent.models import SubjectCategory
from dss.core.provider_preferences.models import (
    PreferenceBook,
    ProviderId,
    SenderPreference,
    capability_key,
)
from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
)

# The registry's names for the entity and its key field. Everything else in
# the row shape is structural (filters/or, totalCount/data).
_ENTITY = "SenderPreference"
_ID_FIELD = "senderId"

# The reserved row every deployment's fallback ladder lives under.
DEFAULT_SENDER_ID = "default"

_ACTIVE = "active"

logger = logging.getLogger(__name__)

# Capabilities an ask can actually carry. A registry row naming any other is
# a row that can never match — worth a log line, not a failed lookup: the
# registry is edited live, and one typo must not cost the valid entries
# alongside it.
_CAPABILITIES = frozenset(
    capability_key(category.value) for category in SubjectCategory
)

# One sender's capability key -> ordered provider ids, as parsed off a row.
Rankings = dict[str, tuple[ProviderId, ...]]


class RegistryProviderPreferences:
    """A `ProviderPreferences` over the registry's search API, per turn."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def for_sender(
        self, sender_id: str | None, *, transaction_id: str
    ) -> SenderPreference:
        """The sender's ranking resolved against the default, both fetched now.

        The search always goes out, even for a sender the last turn asked
        about: the registry is the live source and the DSS holds no cache, so
        an operator's edit applies on the very next turn (ADR-0020).
        """

        asking = tuple(dict.fromkeys(filter(None, (sender_id, DEFAULT_SENDER_ID))))
        rows = await self._search(asking, transaction_id=transaction_id)
        own = rows.get(sender_id) if sender_id else None
        book = PreferenceBook(
            default=rows.get(DEFAULT_SENDER_ID, {}),
            senders={sender_id: own} if sender_id and own is not None else {},
        )
        return book.for_sender(sender_id)

    async def _search(
        self, sender_ids: tuple[str, ...], *, transaction_id: str
    ) -> dict[str, Rankings]:
        """The active rows for these ids, as id → rankings.

        An id with no row is simply absent — the registry answers an unknown
        id with an empty result, never an error. A call that errors, times
        out, or returns garbage comes back empty, logged: an unreachable
        registry must cost the ranking, never the answer.
        """

        endpoint = f"{self._base_url}/api/v1/{_ENTITY}/search"
        body = {"filters": {_ID_FIELD: {"or": list(sender_ids)}}}
        log_external_request(
            "provider_preferences",
            transaction_id,
            endpoint=endpoint,
            sender_ids=",".join(sender_ids),
        )
        try:
            response = await self._client.post(
                endpoint, json=body, timeout=self._timeout_seconds
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log_external_response(
                "provider_preferences",
                transaction_id,
                status="error",
                sender_ids=",".join(sender_ids),
                detail=str(exc),
            )
            return {}

        rows = _rows_by_sender(payload)
        log_external_response(
            "provider_preferences",
            transaction_id,
            status=response.status_code,
            sender_ids=",".join(sender_ids),
            found=",".join(sorted(rows)) or "-",
        )
        _warn_on_unmatchable(rows)
        return rows


def _rows_by_sender(payload: Any) -> dict[str, Rankings]:
    """Every active row in the response, keyed by its sender id.

    Lenient on purpose: the registry is edited while the DSS runs, so a
    malformed entry is skipped rather than sinking the whole row, and a
    malformed row reads as absent — a turn has nowhere to refuse to boot.
    """

    if not isinstance(payload, dict):
        return {}
    return {
        row[_ID_FIELD]: _rankings_of(row)
        for row in payload.get("data") or ()
        if isinstance(row, dict)
        and isinstance(row.get(_ID_FIELD), str)
        and row.get("status", _ACTIVE) == _ACTIVE
    }


def _rankings_of(row: dict[str, Any]) -> Rankings:
    """One row's capability key → ordered providers, skipping what cannot
    rank: a retired entry, a blank capability, an empty provider list."""

    return dict(
        entry
        for entry in map(_ranked_entry, row.get("preferences") or ())
        if entry is not None
    )


def _ranked_entry(entry: Any) -> tuple[str, tuple[ProviderId, ...]] | None:
    if not isinstance(entry, dict) or entry.get("status", _ACTIVE) != _ACTIVE:
        return None
    capability = entry.get("capability")
    providers = tuple(
        provider.strip()
        for provider in (entry.get("providers") or ())
        if isinstance(provider, str) and provider.strip()
    )
    if not isinstance(capability, str) or not capability.strip() or not providers:
        return None
    return capability_key(capability), providers


def _warn_on_unmatchable(rows: dict[str, Rankings]) -> None:
    """Say which entries can never match an ask.

    Kept in the rankings anyway — they are harmless — but said out loud: a
    typo'd capability in a live registry row otherwise looks exactly like a
    ranking nobody wrote.
    """

    for sender_id, rankings in rows.items():
        for capability in sorted(rankings.keys() - _CAPABILITIES):
            logger.warning(
                "registry ranking for %s names the capability %r, which no ask carries",
                sender_id,
                capability,
            )
