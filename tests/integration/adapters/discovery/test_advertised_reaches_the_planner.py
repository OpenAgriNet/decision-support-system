"""Tier 2 — the join between discovery's output and the planner's rendering.

``map_discover_response`` writes ``ProviderCapability.advertised``;
``render_candidates_as_markdown`` reads it. Each has its own unit tests, but
both build their own fixture by hand, so a drift between the shape the adapter
produces and the shape the renderer expects leaves both suites green.

This drives a real recorded ``on_discover`` response through both, with no
doubles between them, and asserts the provider's advertised values come out
the far end — which is the whole point of keeping them.

The fixture is WeatherObservation, not MandiPrice: the commodity case is what
prompted the work, but nothing here is commodity-specific, and a pack whose
vocabulary is plain strings rather than code/name pairs proves it.

Recorded data rather than a hand-written one on purpose. The first run of this
file failed on ``geographicGranularity: ["Point"]`` — a single-element list
that is not a vocabulary at all, which neither side's own fixtures contained.
That case is now asserted as-is; see the test that names it.
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

    Asserted rather than fixed, deliberately. Telling the two apart needs an
    authority on which advertised fields are filterable; the pack has one
    (``profile.json``), but reaching it from here means a new index threaded
    through the adapter — the same cost that made a skip-list the choice in
    ``client.py``. The accepted cost is one extra rendered line the model may
    read as filterable.

    This test exists so that cost stays visible. If a pack ever advertises
    something misleading enough to matter, this is the assertion that has to
    change, and the comment above says what it would take.
    """

    markdown = _rendered()

    assert "geographicGranularity: Point" in markdown
