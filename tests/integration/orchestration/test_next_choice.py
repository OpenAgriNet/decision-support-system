"""Tier 3 — the providers `select` held back for an ask, and the offer to
switch to them. Plain values in and out; the planner wiring is covered in
`test_planner_preferred_providers.py`."""

from __future__ import annotations

from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)
from dss.orchestration.next_choice import (
    Consulted,
    NextChoices,
    render_with_next_choice_offer,
)
from dss.ports.invocation import SelectFailedError

ADVISORY = "openagrinet:KnowledgeAdvisory"


def _capability(provider: str) -> ProviderCapability:
    return ProviderCapability(
        provider_id=provider,
        provider_name=provider.title(),
        capability=ADVISORY,
        resource_id=f"res:{provider}",
    )


def _answered(provider: str) -> Consulted:
    return Consulted(
        capability=_capability(provider),
        answers=(
            DiscoveredAnswer(
                provider_id=provider,
                provider_name=provider.title(),
                capability=ADVISORY,
                resource_id=f"res:{provider}:passage",
                attributes={"message": f"advice from {provider}"},
                validity=None,
            ),
        ),
    )


def _empty(provider: str) -> Consulted:
    return Consulted(capability=_capability(provider))


def _failed(provider: str) -> Consulted:
    return Consulted(
        capability=_capability(provider),
        failure=SelectFailedError(ADVISORY, 503, FailureClass.TRANSIENT, "down"),
    )


def _provider(choice: Consulted | None) -> str | None:
    return None if choice is None else choice.capability.provider_id


def test_take_walks_down_the_ranking_then_runs_out() -> None:
    first, second, third = _answered("mh"), _answered("bv"), _answered("icar")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second, third), chosen=first)

    taken = [_provider(choices.take(0)) for _ in range(3)]

    assert taken == ["bv", "icar", None]


def test_only_providers_that_returned_data_are_held() -> None:
    """An empty or unreachable provider has nothing to fall back to."""

    first = _answered("mh")
    consulted = (first, _empty("kvk"), _failed("agmarknet"), _answered("bv"))
    choices = NextChoices()
    choices.hold_after(0, consulted=consulted, chosen=first)

    assert _provider(choices.take(0)) == "bv"
    assert choices.take(0) is None


def test_providers_ranked_above_the_chosen_one_are_not_held() -> None:
    """They had nothing, which is why a lower one was chosen."""

    chosen = _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(_empty("mh"), chosen), chosen=chosen)

    assert not choices.has_any(0)


def test_holding_again_for_an_ask_replaces_what_was_held() -> None:
    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)
    choices.hold_after(0, consulted=(second,), chosen=second)

    assert not choices.has_any(0)


def test_asks_are_held_apart() -> None:
    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)

    assert choices.take(1) is None
    assert _provider(choices.take(0)) == "bv"


def test_the_offer_is_made_while_a_next_choice_is_left() -> None:
    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)

    assert "select_next" in render_with_next_choice_offer(
        choices, ask_index=0, answers=first.answers
    )


def test_the_offer_goes_once_the_ranking_is_spent() -> None:
    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)
    choices.take(0)

    reply = render_with_next_choice_offer(choices, ask_index=0, answers=second.answers)

    assert "select_next" not in reply
    assert "advice from bv" in reply


def test_the_offer_sits_outside_the_retrieved_data() -> None:
    """Inside the markers it would read as provider text, not ours."""

    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)

    reply = render_with_next_choice_offer(choices, ask_index=0, answers=first.answers)

    assert reply.index("<END RETRIEVED DATA>") < reply.index("select_next")
