"""Types produced and consumed by provider_preferences.

Data shapes only. Behaviour lives in service.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Literal

# What a preference list is keyed on: the ask's own subject category —
# "Weather", "Market". That is the word an adopter uses for a topic, and it
# is what `/discover` already filtered on.
#
# Deliberately not the resolved `@type`: an adopter picks providers for
# weather, not for `openagrinet:WeatherObservation`, and one category can
# resolve to several @types. A plain alias, like `NetworkSchemaType` — it
# buys the name, not a runtime check.
Topic = str

# A provider's id as the network publishes it, and as `ProviderCapability`
# and `DiscoveredAnswer` both carry it. The one value the two sides join on.
ProviderId = str

# Which layer a ranking came from. Closed on purpose, unlike the id aliases
# above: there are exactly two layers, and the type checker can hold that.
RankingSource = Literal["sender", "default"]


def topic_key(topic: str) -> str:
    """How two topic names are compared.

    Case and surrounding space are a config author's business, not a routing
    decision: `weather` in a registry row means the `Weather` the
    classifier emits.
    """

    return topic.strip().casefold()


@dataclass(frozen=True)
class SenderPreference:
    """One sender's ranked providers per topic, already resolved against the
    default underneath.

    Resolved, not layered: `ranking_for` answers in one lookup, so nothing
    downstream has to know a default existed. Which layer a ranking came from
    is recorded in `source` for the operator, never consulted for routing.

    `known` says whether the sender id matched a configured sender at all. It
    separates "this sender asked for exactly the default" from "we have never
    heard of this sender", which read identically off `by_topic` alone and
    mean different things to an operator reading a log line.
    """

    sender_id: str | None
    known: bool
    by_topic: Mapping[str, tuple[ProviderId, ...]]  # keyed by `topic_key`
    # Keyed by `topic_key` too. A dict default needs a factory: the dataclass
    # is frozen, which stops reassignment, not sharing one instance.
    source: Mapping[str, RankingSource] = dataclass_field(default_factory=dict)

    def ranking_for(self, topic: Topic) -> tuple[ProviderId, ...]:
        """The providers this sender wants for this topic, best first.

        Empty means nobody expressed a preference for it — neither the sender
        nor the default. That is not "prefer nothing": it is the state the
        system was in before this feature, and `service.prioritise` leaves
        such an ask alone.
        """

        return self.by_topic.get(topic_key(topic), ())

    def source_for(self, topic: Topic) -> RankingSource | None:
        """Which layer answered `ranking_for` — for the log line, not routing."""

        return self.source.get(topic_key(topic))


# No file mounted, or a sender nobody configured and no default either. Every
# ask keeps the candidates discovery found, in the order it found them.
NO_PREFERENCE = SenderPreference(sender_id=None, known=False, by_topic={})


@dataclass(frozen=True)
class PreferenceBook:
    """Every configured sender's rankings, and the default underneath them.

    The merge rule lives here rather than in whichever adapter read the
    rankings, so no reader of the registry can drift on what "falls back
    to the default" means.
    """

    default: Mapping[str, tuple[ProviderId, ...]]  # topic key -> providers
    senders: Mapping[str, Mapping[str, tuple[ProviderId, ...]]]  # sender -> the same

    def for_sender(self, sender_id: str | None) -> SenderPreference:
        """This sender's preference, with the default filling every topic the
        sender did not rank.

        Per topic, not per sender: a sender that ranked only `Weather` still
        gets the default's `Market` list. The two layers never concatenate —
        a sender naming one weather provider means that one, not that one
        followed by whatever the default also listed. The list is the whole
        answer for its topic.
        """

        own = self.senders.get(sender_id or "", {})
        by_topic = {**self.default, **own}
        source: dict[str, RankingSource] = {
            topic: "sender" if topic in own else "default" for topic in by_topic
        }
        return SenderPreference(
            sender_id=sender_id,
            known=sender_id is not None and sender_id in self.senders,
            by_topic=by_topic,
            source=source,
        )


@dataclass(frozen=True)
class AskPriority:
    """What the sender's preference did to one ask's candidates.

    Kept whole rather than reduced to the reordered list, because the
    interesting part is what is *missing*: a provider discovery found and the
    ranking excluded answered nothing, and an operator looking at an
    unexpectedly thin answer needs to see that it was dropped on purpose.
    """

    ask_index: int
    topic: Topic
    sender_id: str | None
    source: RankingSource | None  # None when no ranking applied
    ranking: tuple[ProviderId, ...]  # what the sender asked for, in order
    kept: tuple[ProviderId, ...]  # ranked providers discovery actually found
    dropped: tuple[ProviderId, ...]  # found, but not on the ranking

    @property
    def applied(self) -> bool:
        """Whether a ranking governed this ask at all.

        False leaves the ask exactly as discovery left it — the behaviour
        before any preference existed — so every caller branches on this
        rather than on an empty `kept`, which also happens when a ranking
        applied and matched nobody.
        """

        return bool(self.ranking)
