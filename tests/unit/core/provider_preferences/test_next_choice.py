"""Tier 1 — the providers `select` held back for an ask. The planner wiring
is covered in
`tests/integration/orchestration/test_planner_preferred_providers.py`."""

from __future__ import annotations

from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    FailureClass,
    ProviderCapability,
)
from dss.core.provider_preferences.next_choice import (
    Consulted,
    NextChoices,
)
from dss.ports.invocation import SelectFailedError
from tests.support.providers import build_capability

ADVISORY = "openagrinet:KnowledgeAdvisory"


def _capability(provider: str) -> ProviderCapability:
    return build_capability(
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

    assert choices.take(0) is None


def test_holding_again_for_an_ask_replaces_what_was_held() -> None:
    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)
    choices.hold_after(0, consulted=(second,), chosen=second)

    assert choices.take(0) is None


def test_asks_are_held_apart() -> None:
    first, second = _answered("mh"), _answered("bv")
    choices = NextChoices()
    choices.hold_after(0, consulted=(first, second), chosen=first)

    assert choices.take(1) is None
    assert _provider(choices.take(0)) == "bv"
