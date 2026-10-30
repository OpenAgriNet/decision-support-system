"""Falling back down a sender's ranking when the first answer cannot serve
the ask (ADR-0019).

`select` consults every ranked provider at once and answers from the first
that returned data. The others that returned data wait here, so
`select_next` can switch to one without calling anybody again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dss.core.planner.markdown import render_answers_as_markdown
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

    def has_any(self, ask_index: int) -> bool:
        return bool(self.waiting.get(ask_index))


def render_with_next_choice_offer(
    next_choices: NextChoices, *, ask_index: int, answers: tuple[DiscoveredAnswer, ...]
) -> str:
    """The data, and — when a lower-ranked provider also answered — how to
    switch to it. Outside the retrieved-data markers, so it reads as ours."""

    data = render_answers_as_markdown(answers)
    if not next_choices.has_any(ask_index):
        return data
    return (
        f"{data}\n\nIf this data cannot answer ask {ask_index}, call "
        f"select_next with ask_index={ask_index} to use the next preferred "
        f"provider's data instead. If it answers the ask, do not."
    )


def no_next_choice(ask_index: int) -> str:
    return (
        f"No other preferred provider has data for ask {ask_index}. Do not "
        f"call select_next for it again; report what you found."
    )
