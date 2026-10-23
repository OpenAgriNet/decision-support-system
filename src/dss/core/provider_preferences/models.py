"""Types produced and consumed by provider_preferences.

Data shapes only. Behaviour lives in service.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Literal

# What a preference list is keyed on — the registry's word for the *kind of
# help* a ranking covers, carried by an ask as its subject category:
# "Weather", "Market". It is what `/discover` already filtered on.
#
# NOT the resolved `@type` that `provider_discovery` calls a capability
# (`openagrinet:WeatherObservation`): an adopter picks providers for weather,
# not for a schema, and one of these can resolve to several @types. A plain
# alias, like `NetworkSchemaType` — it buys the name, not a runtime check.
Capability = str

# A provider's id as the network publishes it, and as `ProviderCapability`
# and `DiscoveredAnswer` both carry it. The one value the two sides join on.
ProviderId = str

# Which layer a ranking came from. Closed on purpose, unlike the id aliases
# above: there are exactly two layers, and the type checker can hold that.
RankingSource = Literal["sender", "default"]


def capability_key(capability: str) -> str:
    """How two capability names are compared.

    Case and surrounding space are a registry author's business, not a
    routing decision: `weather` in a registry row means the `Weather` the
    classifier emits.
    """

    return capability.strip().casefold()


@dataclass(frozen=True)
class SenderPreference:
    """One sender's ranked providers per capability, already resolved against
    the default underneath.

    Resolved, not layered: `ranking_for` answers in one lookup, so nothing
    downstream has to know a default existed. Which layer a ranking came from
    is recorded in `source` for the operator, never consulted for routing.

    `known` says whether the sender id matched a registered sender at all. It
    separates "this sender asked for exactly the default" from "we have never
    heard of this sender", which read identically off `by_capability` alone
    and mean different things to an operator reading a log line.
    """

    sender_id: str | None
    known: bool
    by_capability: Mapping[str, tuple[ProviderId, ...]]  # keyed by `capability_key`
    # Keyed by `capability_key` too. A dict default needs a factory: the
    # dataclass is frozen, which stops reassignment, not sharing one instance.
    source: Mapping[str, RankingSource] = dataclass_field(default_factory=dict)

    def ranking_for(self, capability: Capability) -> tuple[ProviderId, ...]:
        """The providers this sender wants for this capability, best first.

        Empty means nobody expressed a preference for it — neither the sender
        nor the default. That is not "prefer nothing": it is the state the
        system was in before this feature, and `service.prioritise` leaves
        such an ask alone.
        """

        return self.by_capability.get(capability_key(capability), ())

    def source_for(self, capability: Capability) -> RankingSource | None:
        """Which layer answered `ranking_for` — for the log line, not routing."""

        return self.source.get(capability_key(capability))


# No registry configured, or a sender nobody registered and no default either.
# Every ask keeps the candidates discovery found, in the order it found them.
NO_PREFERENCE = SenderPreference(sender_id=None, known=False, by_capability={})


@dataclass(frozen=True)
class PreferenceBook:
    """Every registered sender's rankings, and the default underneath them.

    The merge rule lives here rather than in whichever adapter read the
    rankings, so no reader of the registry can drift on what "falls back
    to the default" means.
    """

    default: Mapping[str, tuple[ProviderId, ...]]  # capability key -> providers
    senders: Mapping[str, Mapping[str, tuple[ProviderId, ...]]]  # sender -> the same

    def for_sender(self, sender_id: str | None) -> SenderPreference:
        """This sender's preference, with the default filling every capability
        the sender did not rank.

        Per capability, not per sender: a sender that ranked only `Weather`
        still gets the default's `Market` list. The two layers never
        concatenate — a sender naming one weather provider means that one, not
        that one followed by whatever the default also listed. The list is the
        whole answer for its capability.
        """

        own = self.senders.get(sender_id or "", {})
        by_capability = {**self.default, **own}
        source: dict[str, RankingSource] = {
            key: "sender" if key in own else "default" for key in by_capability
        }
        return SenderPreference(
            sender_id=sender_id,
            known=sender_id is not None and sender_id in self.senders,
            by_capability=by_capability,
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
    capability: Capability
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
