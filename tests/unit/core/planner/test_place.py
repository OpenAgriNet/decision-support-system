"""Tier 1 — which asks may go on without a place.

The schema pack decides: an option from a pack that indexes no location can
serve an ask that names nowhere. The rule itself lives on ``DomainSchema``
and is tested with it; this is the per-ask reading of it over discovery.
"""

from __future__ import annotations

from dss.core.intent.models import Ask, Intent, InteractionType, SubjectCategory
from dss.core.planner.place import place_optional_asks
from dss.core.planner.validation import DomainSchema
from dss.core.provider_discovery.models import (
    DiscoveredAnswer,
    DiscoveryResult,
    ProviderCapability,
)

_ADVISORY = "openagrinet:KnowledgeAdvisory"
_WEATHER = "openagrinet:WeatherObservation"

_SCHEMAS = {
    _ADVISORY: DomainSchema(type="KnowledgeAdvisory", filterable=()),
    _WEATHER: DomainSchema(type="WeatherObservation", filterable=(), needs_place=True),
}


def _ask(place=None) -> Ask:  # noqa: ANN001
    return Ask(
        subject_categories=SubjectCategory.CROP,
        interaction_type=InteractionType.ADVISE,
        place=place,
    )


def _capability(schema_type: str) -> ProviderCapability:
    return ProviderCapability(
        provider_id="p",
        provider_name="Provider",
        capability=schema_type,
        resource_id=f"res:{schema_type}",
    )


def _discovery(**capabilities: tuple[ProviderCapability, ...]) -> DiscoveryResult:
    return DiscoveryResult(
        answers={},
        capabilities={int(k): v for k, v in capabilities.items()},
        failures={},
        events=(),
    )


def test_one_placeless_option_is_enough() -> None:
    """ "How do I grow potato?" with nothing named: an advisory serves it
    from nowhere, so the ask goes on."""

    intent = Intent(asks=(_ask(),))
    discovery = _discovery(**{"0": (_capability(_ADVISORY),)})

    assert place_optional_asks(intent, discovery, _SCHEMAS) == frozenset({0})


def test_a_direct_answer_counts_as_an_option() -> None:
    """A catalog answer needs no select, but it is still from a pack: an
    advisory one serves the ask, a weather one for somewhere would not."""

    intent = Intent(asks=(_ask(),))
    answer = DiscoveredAnswer(
        provider_id="p",
        provider_name="Provider",
        capability=_ADVISORY,
        resource_id="res:advice",
        attributes={},
        validity=None,
    )
    discovery = DiscoveryResult(
        answers={0: (answer,)}, capabilities={}, failures={}, events=()
    )

    assert place_optional_asks(intent, discovery, _SCHEMAS) == frozenset({0})


def test_an_option_with_no_loaded_schema_needs_a_place() -> None:
    """Discovery can name a @type whose pack was skipped on disk. Nothing says
    it serves an ask from nowhere, so it does not: the farmer is asked, as
    before this rule existed."""

    intent = Intent(asks=(_ask(),))
    discovery = _discovery(**{"0": (_capability("openagrinet:Unknown"),)})

    assert place_optional_asks(intent, discovery, _SCHEMAS) == frozenset()


def test_every_option_needs_place() -> None:
    """ "Will it rain?" with nothing named: weather is for somewhere."""

    intent = Intent(asks=(_ask(),))
    discovery = _discovery(**{"0": (_capability(_WEATHER),)})

    assert place_optional_asks(intent, discovery, _SCHEMAS) == frozenset()


def test_no_option_found_is_not_a_place_failure() -> None:
    """Nobody serves the ask. A place would not change that, so asking for
    one is wrong: the ask ends as unserved, as it did before this rule."""

    intent = Intent(asks=(_ask(),))
    discovery = _discovery(**{"0": ()})

    assert place_optional_asks(intent, discovery, _SCHEMAS) == frozenset({0})


def test_one_option_needing_no_place_is_enough() -> None:
    """A crop ask discovers advisory and facility providers alike. The
    advisory one can answer, so the ask goes on; the planner's own guard
    keeps the facility one from being called without a point."""

    intent = Intent(asks=(_ask(),))
    discovery = _discovery(**{"0": (_capability(_WEATHER), _capability(_ADVISORY))})

    assert place_optional_asks(intent, discovery, _SCHEMAS) == frozenset({0})
