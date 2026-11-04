"""Falling back down a sender's ranking when the first answer cannot serve
the ask.

`select` consults every ranked provider at once and answers from the first
that returned data. The others that returned data wait here, so
`select_next` can switch to one without calling anybody again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dss.core.provider_discovery.models import DiscoveredAnswer, ProviderCapability
from dss.ports.invocation import SelectFailedError


@dataclass
class Consulted:
    """One provider's outcome for one ask, kept whether or not it was used.

    The unused ones are the point: the team compares them later to judge
    whether the ranking is ordered the way it should be, and `select_next`
    falls back to them. One ask is answered by one provider.
    """

    capability: ProviderCapability
    answers: tuple[DiscoveredAnswer, ...] = ()
    failure: SelectFailedError | None = None


@dataclass
class NextChoices:
    """Per ask, the lower-ranked providers that also returned data, best first."""

    waiting: dict[int, tuple[Consulted, ...]] = field(default_factory=dict)

    def hold_after(
        self, ask_index: int, *, consulted: tuple[Consulted, ...], chosen: Consulted
    ) -> None:
        position = consulted.index(chosen)
        self.waiting[ask_index] = tuple(
            one for one in consulted[position + 1 :] if one.answers
        )

    def take(self, ask_index: int) -> Consulted | None:
        """The next choice for the ask, removed so a second call moves on."""

        waiting = self.waiting.get(ask_index, ())
        if not waiting:
            return None
        self.waiting[ask_index] = waiting[1:]
        return waiting[0]


def no_next_choice(ask_index: int) -> str:
    return (
        f"No other preferred provider has data for ask {ask_index}. Do not "
        f"call select_next for it again; report what you found."
    )
