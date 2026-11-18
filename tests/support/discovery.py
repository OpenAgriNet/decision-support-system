from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from dss.core.intent.models import Ask
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryEvent,
    DiscoveryFailure,
    DiscoveryResult,
    ProviderCapability,
)
from dss.core.provider_preferences.models import AskPriority
from tests.support.asks import an_ask


def a_priority(*ranking: str, **overrides: Any) -> AskPriority:
    """What a sender's ranking did to ask 0. Every ranked provider was found
    and none was dropped, unless a field is overridden. With no ranking, none
    applied."""

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


@dataclass(frozen=True)
class FoundForAsk:
    ask: Ask
    answers: tuple[DiscoveredAnswer, ...]
    capabilities: tuple[ProviderCapability, ...]
    failures: tuple[DiscoveryFailure, ...]
    priority: AskPriority | None


def discovered(
    ask: Ask | int = 0,
    *,
    answers: Sequence[DiscoveredAnswer] = (),
    capabilities: Sequence[ProviderCapability] = (),
    failures: Sequence[DiscoveryFailure] = (),
    priority: AskPriority | None = None,
) -> FoundForAsk:
    """What discovery found for one ask. An id stands for any ask with that
    id, for a test that does not care which ask it is."""

    return FoundForAsk(
        ask=ask if isinstance(ask, Ask) else an_ask(id=ask),
        answers=tuple(answers),
        capabilities=tuple(capabilities),
        failures=tuple(failures),
        priority=priority,
    )


def a_discovery(
    *found: FoundForAsk, events: Sequence[DiscoveryEvent] = ()
) -> DiscoveryResult:
    """A result with an entry for each ask given, as discovery writes one.
    Tests build results here so a change to its shape is made in one place."""

    return DiscoveryResult(
        answers={entry.ask.id: entry.answers for entry in found},
        capabilities={entry.ask.id: entry.capabilities for entry in found},
        failures={entry.ask.id: entry.failures for entry in found},
        events=tuple(events),
        priorities={
            entry.ask.id: entry.priority
            for entry in found
            if entry.priority is not None
        },
    )
