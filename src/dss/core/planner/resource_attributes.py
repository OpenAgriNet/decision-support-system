"""Assembles resourceAttributes for a /select call.

Structural fields (@context, @type, subjectCategories, location) come from
discovery data and the turn — never from the model. The model's own
resource_attributes (a resolved commodity code, topics, ...) merge on top,
but cannot override a structural field: the model chooses the capability by
resource_id, not by rewriting @type after the fact.
"""

from __future__ import annotations

from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.models import UserTurn

# select is only called for an OnDemand capability — a Direct resource's
# values are already in the catalog, so there is nothing to select.
_ON_DEMAND = "OnDemand"


def _location_field(turn: UserTurn) -> dict | None:
    """A Beckn ``Location``, which carries the geometry under ``geo``.

    Every pack's ``location`` resolves to ``CompleteLocation``, an
    ``allOf`` over Beckn's ``Location`` that makes ``geo`` required. The
    geometry is therefore nested, not the value of ``location`` itself.
    """

    if turn.location is None or turn.location.geometry is None:
        return None
    return {
        "geo": {
            "type": turn.location.geometry.type,
            "coordinates": turn.location.geometry.coordinates,
        }
    }


def build_resource_attributes(
    *,
    capability: ProviderCapability,
    turn: UserTurn,
    model_filled: dict,
    schema_context_index: dict[str, str],
    filterable: tuple[str, ...],
) -> dict:
    """Build the full resourceAttributes object: structural fields first, the
    model's fields merged on top — but structural fields always win.

    ``@context`` is the URL the pack declares, taken verbatim rather than
    rebuilt from a base URL and the pack's name and version.

    ``informationMode`` is constant here: ``select`` is only ever called for
    an OnDemand capability. A Direct resource's values are already in the
    catalog and arrive as a ``DiscoveredAnswer``, so there is nothing to
    select.
    """

    structural: dict = {
        "@context": schema_context_index[capability.capability],
        "@type": capability.capability,
        "informationMode": _ON_DEMAND,
        "subjectCategories": list(capability.observed_categories),
    }

    # A fallback, not a structural field. An OnDemand weather resource
    # advertises no `location` — there is no fixed point until someone asks —
    # so the turn's geometry is what says which place the forecast is for.
    #
    # It must not override, though: where a pack's `location` identifies the
    # resource rather than the query, the advertised value is the right one.
    # `AgricultureFacility.location` says so in words — "do not populate it
    # with the search origin or another inferred point" — and substituting
    # there would claim the facility sits wherever the farmer is asking from.
    fallback: dict = {}
    location = _location_field(turn)
    if location is not None:
        fallback["location"] = location

    # Three layers, each overriding the one before.
    #
    # The discovered attributes are the base: a provider judges a `select`
    # against the resource it advertised, and what it requires is not always
    # what `profile.json` lists as filterable — MandiPrice requires
    # `market.marketName` and never offers it, so the model could not supply it
    # and every call was rejected. Echoing the discovered `market` object
    # carries the required part through without the model guessing it.
    #
    # Only the *filterable* ones, matched on the top-level name so a whole
    # object rides along with the parts inside it. A resource advertises two
    # kinds of thing side by side: values a caller may filter on (`market`,
    # `supportedCommodities`) and facts about the provider (`forecastHorizon:
    # P5D`, `updateFrequency: PT12H`). Sending a fact back states it as a
    # filter criterion it never was, and a pack declaring
    # `additionalProperties: false` rejects the call for it.
    #
    # The model's values replace a discovered one outright rather than merging
    # into it: the provider advertises every commodity it serves, and the
    # farmer asked about one, so `supportedCommodities` narrows to that one.
    allowed = {path.split(".")[0].split("[")[0] for path in filterable}
    echoed = {
        field: value
        for field, value in capability.advertised.items()
        if field in allowed
    }
    return {**fallback, **echoed, **model_filled, **structural}
