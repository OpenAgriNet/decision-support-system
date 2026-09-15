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

    location = _location_field(turn)
    if location is not None:
        structural["location"] = location

    # Three layers, each overriding the one before. The discovered attributes
    # are the base: a provider judges a `select` against the resource it
    # advertised, and fields it requires are not always ones `profile.json`
    # lists as filterable — `market.marketName` is required by the MandiPrice
    # schema and absent from its filterable paths, so the model could not
    # supply it and every call was rejected. Echoing what discovery returned
    # carries those through without the model having to guess them.
    #
    # The model's values replace a discovered one outright rather than merging
    # into it: the provider advertises every commodity it serves, and the
    # farmer asked about one, so `supportedCommodities` narrows to that one.
    return {**capability.advertised, **model_filled, **structural}
