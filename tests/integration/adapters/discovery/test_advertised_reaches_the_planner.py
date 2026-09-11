"""Tier 2 — the join between discovery's output and the planner's rendering.

``map_discover_response`` writes ``ProviderCapability.advertised``;
``render_candidates_as_markdown`` reads it. Each has its own unit tests, but
both build their own fixture by hand, so a drift between the shape the adapter
produces and the shape the renderer expects leaves both suites green.

This drives a real recorded ``on_discover`` response through both, with no
doubles between them, and asserts the provider's advertised values come out
the far end — which is the whole point of keeping them.

Two recorded catalogs, because the renderer now makes two distinct decisions.
WeatherObservation covers what must reach the model: the commodity case is what
prompted the work, but nothing here is commodity-specific, and a pack whose
vocabulary is plain strings rather than code/name pairs proves it.
KnowledgeAdvisory covers what must not — its ``topics`` is the provider
describing its own content under a filterable path.

Recorded data rather than hand-written on purpose, and it has earned its keep
twice. The first run of this file failed on ``geographicGranularity: ["Point"]``
— a single-element list that is not a vocabulary at all, which neither side's
own fixtures contained; that case is asserted as-is below. Later, a first
attempt at the ``topics`` fix keyed on item shape and was caught here by
``supportedParameters: ["Rainfall", "Temperature"]`` — a genuine vocabulary of
bare strings that the shape rule would have thrown away.
"""

from __future__ import annotations

import json
from pathlib import Path

from dss.adapters.discovery.client import map_discover_response
from dss.core.planner.describe_capability import render_candidates_as_markdown
from dss.core.planner.validation import DomainSchema

FIXTURES = Path(__file__).parent / "fixtures"

_WEATHER = DomainSchema(
    type="WeatherObservation",
    filterable=("observationType", "parameters"),
)


def _rendered() -> str:
    response = json.loads((FIXTURES / "discover_response.json").read_text())
    result = map_discover_response(response, ask_indices=(0,))
    return render_candidates_as_markdown(
        result.capabilities[0],
        schemas={"openagrinet:WeatherObservation": _WEATHER},
    )


def test_a_providers_advertised_values_reach_the_rendered_candidate() -> None:
    """The values the network advertised are what the model is shown."""

    markdown = _rendered()

    assert "supportedParameters: Rainfall, Temperature" in markdown
    assert "supportedObservationTypes: Forecast" in markdown


def test_a_structural_attribute_does_not_reach_the_rendered_candidate() -> None:
    """Beckn-level fields are carried by every resource whatever its pack.
    Rendering them would tell the model to filter on the envelope rather than
    on the provider's offering.

    Asserted on ``subjectCategories`` specifically, because it is the only
    structural field in this fixture that is a *list*. The renderer drops
    scalars anyway, so an assertion on ``@type`` or ``informationMode`` passes
    even with the adapter's skip-list disabled — it re-tests the list rule
    rather than the skip-list. This one fails when the skip-list goes, which
    is the point of naming it here.
    """

    markdown = _rendered()

    assert "subjectCategories: Weather" not in markdown


def test_a_scalar_attribute_does_not_reach_the_rendered_candidate() -> None:
    """A scalar is a fact about the provider, not a value a select call may
    carry, so it must not appear under a heading that promises otherwise."""

    markdown = _rendered()

    assert "forecastHorizon" not in markdown
    assert "updateFrequency" not in markdown


def test_a_single_element_list_is_rendered_even_when_it_is_not_a_vocabulary() -> None:
    """``geographicGranularity: ["Point"]`` describes how the provider reports
    data, not what may be asked for — but it arrives as a list, and the
    renderer's rule is that a list is a vocabulary.

    Asserted rather than fixed, deliberately, and still true now that the
    renderer does consult the pack's filterable paths: that test catches a
    field advertised *under* a filterable path (``topics``), and
    ``geographicGranularity`` governs no filter at all, so nothing names it.
    Catching it needs the opposite authority — which advertised fields are a
    vocabulary *for* something — and no pack declares that today.

    The accepted cost is one extra rendered line the model may read as
    filterable. This test exists so that cost stays visible; if a pack ever
    advertises something misleading enough to matter, this is the assertion
    that has to change.
    """

    markdown = _rendered()

    assert "geographicGranularity: Point" in markdown


_ADVISORY = DomainSchema(
    type="KnowledgeAdvisory",
    filterable=("topics", "agricultureSubjects[].subjectId", "languages"),
)


def _rendered_advisory() -> str:
    response = json.loads((FIXTURES / "discover_response_advisory.json").read_text())
    result = map_discover_response(response, ask_indices=(0,))
    return render_candidates_as_markdown(
        result.capabilities[0],
        schemas={"openagrinet:KnowledgeAdvisory": _ADVISORY},
    )


def test_content_advertised_under_a_filterable_path_does_not_reach_the_model() -> None:
    """The other half of the rule, on a second recorded catalog.

    ``topics`` is the pack's own filterable path, and this resource advertises
    under it — it is saying what it holds so it can be found, not enumerating
    what may be asked for. Shown as a vocabulary, the model answered "can i
    grow potato" with this provider's ``Crop establishment``.

    A second fixture rather than an assertion on the weather one: weather
    advertises no such field, so only this catalog exercises the case.
    """

    markdown = _rendered_advisory()

    assert "Crop establishment" not in markdown
    assert "Nutrient management" not in markdown
    assert "topics" in markdown  # still offered as a field the model may set


def test_a_vocabulary_beside_it_still_reaches_the_model() -> None:
    """Same resource, same response: dropping ``topics`` must not take the crop
    vocabulary with it. ``agricultureSubjects`` sits under the filterable
    ``agricultureSubjects[].subjectId``, which an exact match leaves alone."""

    markdown = _rendered_advisory()

    assert "agricultureSubjects" in markdown
    assert "COTTON" in markdown
