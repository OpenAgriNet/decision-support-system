from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dss.core.intent.models import Ask
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveredAsk,
    DiscoveryEvent,
    DiscoveryFailure,
    DiscoveryResult,
    ProviderCapability,
    ProviderQueryResult,
)
from dss.core.provider_preferences.models import AskPriority
from tests.support.asks import an_ask


def a_priority(*ranking: str, **overrides: Any) -> AskPriority:
    """Every ranked provider was found and none was dropped, unless a field
    is overridden. With no ranking, none applied."""

    fields: dict[str, Any] = {
        "ask_id": 0,
        "capability": "Weather",
        "sender_id": "kisan-mitra-web",
        "source": "sender" if ranking else None,
        "ranking": ranking,
        "kept": ranking,
        "dropped": (),
    }
    return AskPriority(**(fields | overrides))


def discovered(
    ask: Ask | int = 0,
    *,
    answers: Sequence[DiscoveredAnswer] = (),
    capabilities: Sequence[ProviderCapability] = (),
    failures: Sequence[DiscoveryFailure] = (),
    priority: AskPriority | None = None,
) -> DiscoveredAsk:
    """An id stands for any ask with that id. No query: a test that reads the
    query builds its own."""

    return DiscoveredAsk(
        ask=ask if isinstance(ask, Ask) else an_ask(id=ask),
        query=None,
        found=ProviderQueryResult(
            answers=tuple(answers),
            capabilities=tuple(capabilities),
            failures=tuple(failures),
        ),
        priority=priority,
    )


def a_discovery(
    *asks: DiscoveredAsk, events: Sequence[DiscoveryEvent] = ()
) -> DiscoveryResult:
    """Tests build results here so a change to its shape is made in one place."""

    return DiscoveryResult(asks=asks, events=tuple(events))
