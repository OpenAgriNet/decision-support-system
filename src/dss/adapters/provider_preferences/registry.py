"""Reads a sender's provider ranking from the network registry (ADR-0020).

The registry is asked on every turn, the way `/discover` and `/select` are
asked — the rankings are the registry's row data, maintained there and not
mounted into the DSS. Two searches go out in parallel: the turn's `senderId`
and the reserved id `default`, so a sender nobody registered still lands on
the default ladder without waiting for a second round trip. The per-topic
merge of the two stays in `core.provider_preferences` (`PreferenceBook`), so
no reader of these rows can drift on what "falls back to the default" means.

**A ranking lookup never fails a turn.** A search that errors, times out, or
comes back unparseable counts as "no row found" and is logged as a warning;
with both searches lost the turn runs as if no preference existed — the
behaviour before this feature. Failing the turn would turn a registry outage
into a farmer outage over data that only orders providers.

The wire shape mirrors the registry's `SenderPreference` entity:

    POST {base_url}/api/v1/SenderPreference/search
    {"filters": {"senderId": {"eq": "mh-consumer"}}}

    {"totalCount": 1, "data": [{
        "senderId": "mh-consumer", "status": "active",
        "preferences": [{"topic": "Weather",
                         "providers": ["mausamgram", "bharat-vistaar"]}]}]}

`_ENTITY` and `_ID_FIELD` below are the whole coupling to those names — if
the registry publishes the entity differently, they are the only edit.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
import httpx

from dss.core.intent.models import SubjectCategory
from dss.core.provider_preferences.models import (
    PreferenceBook,
    ProviderId,
    SenderPreference,
    topic_key,
)
from dss.observability.trace_log import (
    log_external_request,
    log_external_response,
)

# The registry's names for the entity and its key field. Everything else in
# the row shape is structural (filters/eq, totalCount/data).
_ENTITY = "SenderPreference"
_ID_FIELD = "senderId"

# The reserved row every deployment's fallback ladder lives under.
DEFAULT_SENDER_ID = "default"

_ACTIVE = "active"

logger = logging.getLogger(__name__)

# Topics an ask can actually carry. A registry row naming any other topic is
# a row that can never match — worth a log line, not a failed lookup: the
# registry is edited live, and one typo must not cost the valid topics
# alongside it.
_TOPICS = frozenset(topic_key(category.value) for category in SubjectCategory)


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

        Both searches always go out, even for a sender the last turn asked
        about: the registry is the live source and the DSS holds no cache, so
        an operator's edit applies on the very next turn (ADR-0020).
        """

        rows: dict[str, dict[str, tuple[ProviderId, ...]] | None] = {}
        asking = (sender_id, DEFAULT_SENDER_ID) if sender_id else (DEFAULT_SENDER_ID,)

        async def fetch(one: str) -> None:
            rows[one] = await self._rankings_for(one, transaction_id=transaction_id)

        async with anyio.create_task_group() as group:
            for one in dict.fromkeys(asking):
                group.start_soon(fetch, one)

        default = rows.get(DEFAULT_SENDER_ID) or {}
        own = rows.get(sender_id or "")
        book = PreferenceBook(
            default=default,
            senders={sender_id: own} if sender_id and own is not None else {},
        )
        return book.for_sender(sender_id)

    async def _rankings_for(
        self, sender_id: str, *, transaction_id: str
    ) -> dict[str, tuple[ProviderId, ...]] | None:
        """One search, mapped to one sender's topic → providers, or ``None``
        when no active row exists — the state `PreferenceBook` reads as "this
        sender recorded nothing". Errors land on ``None`` too, logged: an
        unreachable registry must cost the ranking, never the answer.
        """

        endpoint = f"{self._base_url}/api/v1/{_ENTITY}/search"
        body = {"filters": {_ID_FIELD: {"eq": sender_id}}}
        log_external_request(
            "provider_preferences",
            transaction_id,
            endpoint=endpoint,
            sender_id=sender_id,
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
                sender_id=sender_id,
                detail=str(exc),
            )
            return None

        rankings = _rankings_from(payload, sender_id=sender_id)
        log_external_response(
            "provider_preferences",
            transaction_id,
            status=response.status_code,
            sender_id=sender_id,
            found=rankings is not None,
            topics=",".join(sorted(rankings)) if rankings else "-",
        )
        return rankings


def _rankings_from(
    payload: Any, *, sender_id: str
) -> dict[str, tuple[ProviderId, ...]] | None:
    """The first active row's rankings, keyed the way `topic_key` keys them.

    Lenient on purpose: the registry is edited while the DSS runs, so a
    malformed entry is skipped rather than sinking the whole row, and a
    malformed row reads as absent — a turn has nowhere to refuse to boot.
    """

    row = _active_row(payload)
    if row is None:
        return None
    rankings: dict[str, tuple[ProviderId, ...]] = {}
    for entry in row.get("preferences") or ():
        ranked = _ranked_entry(entry)
        if ranked is None:
            continue
        topic, providers = ranked
        if topic not in _TOPICS:
            # Kept anyway — it simply never matches an ask — but said out
            # loud: a typo'd topic in a live registry row otherwise looks
            # exactly like a ranking nobody wrote.
            logger.warning(
                "registry ranking for %s names the topic %r, which no ask carries",
                sender_id,
                topic,
            )
        rankings[topic] = providers
    return rankings


def _active_row(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    for row in payload.get("data") or ():
        if isinstance(row, dict) and row.get("status", _ACTIVE) == _ACTIVE:
            return row
    return None


def _ranked_entry(entry: Any) -> tuple[str, tuple[ProviderId, ...]] | None:
    if not isinstance(entry, dict) or entry.get("status", _ACTIVE) != _ACTIVE:
        return None
    topic = entry.get("topic")
    providers = tuple(
        provider.strip()
        for provider in (entry.get("providers") or ())
        if isinstance(provider, str) and provider.strip()
    )
    if not isinstance(topic, str) or not topic.strip() or not providers:
        return None
    return topic_key(topic), providers
