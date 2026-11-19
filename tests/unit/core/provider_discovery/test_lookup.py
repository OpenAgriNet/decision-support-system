"""Tier 1 — finding the ProviderCapability the model picked by resource_id."""

from __future__ import annotations

from dss.core.provider_discovery.models import ProviderCapability
from tests.support.discovery import a_discovery, discovered
from tests.support.providers import build_capability

MANDI_PRICE = "openagrinet:MandiPrice"
WEATHER = "openagrinet:WeatherForecast"


def _capability(resource_id: str, schema_type: str = MANDI_PRICE) -> ProviderCapability:
    return build_capability(
        provider_id="agmarknet",
        provider_name="Agmarknet",
        capability=schema_type,
        resource_id=resource_id,
        observed_categories=("Market",),
    )


def test_finds_the_matching_capability_for_the_ask() -> None:
    found = discovered(capabilities=(_capability("res:a"), _capability("res:b")))

    result = found.find_capability("res:b")

    assert result is not None
    assert result.resource_id == "res:b"


def test_returns_none_when_resource_id_does_not_match_any_candidate() -> None:
    found = discovered(capabilities=(_capability("res:a"),))

    assert found.find_capability("res:not-there") is None


def test_finds_every_capability_of_a_type_in_order() -> None:
    found = discovered(
        capabilities=(
            _capability("res:a"),
            _capability("res:rain", WEATHER),
            _capability("res:b"),
        )
    )

    of_type = found.find_capabilities_of_type(MANDI_PRICE)

    assert [capability.resource_id for capability in of_type] == ["res:a", "res:b"]


def test_an_ask_the_turn_lacks_is_not_found() -> None:
    discovery = a_discovery(discovered(0))

    assert discovery.find(5) is None
