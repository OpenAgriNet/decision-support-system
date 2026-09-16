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


def _matching_advertised(selector: dict, advertised: list) -> dict | None:
    """The advertised item the model's selector names, or ``None``.

    Matched on whatever keys the selector carries, so no field name is
    hardcoded: MandiPrice identifies a commodity by ``code`` and
    KnowledgeAdvisory a subject by ``subjectId``, and neither is named here.
    """

    for item in advertised:
        if not isinstance(item, dict):
            continue
        if all(item.get(key) == value for key, value in selector.items()):
            return item
    return None


def _narrowed(model_value: object, advertised_value: object) -> object:
    """The model's list read as a selection from the advertised one.

    The model can only send the filterable field — `supportedCommodities[].code`
    — so treating its list as a replacement dropped the rest of each item, and
    the provider got `{"code": "23"}` where it had advertised
    `{"code": "23", "name": "Onion"}`.

    Only where both sides are lists of objects. A field the provider never
    advertised (`parameters`, which no pack carries on an OnDemand resource) has
    nothing to select from, and the model's own value stands. So does an item
    matching nothing advertised: naming something unavailable is the provider's
    call to refuse, not ours to drop without saying so.
    """

    if isinstance(model_value, dict) and isinstance(advertised_value, dict):
        # Merged, with the advertised value winning every conflict. The model
        # names one part of the object — the fields `profile.json` offers — and
        # replacing the whole thing lost `marketName`, `district` and the
        # market's own coordinates. On a key both supply, the advertisement is
        # a fact about the resource and the model's is a guess: one wrote
        # `marketCode: "Sholapur"`, the district from the question, where the
        # provider had published `1806`.
        return {**model_value, **advertised_value}
    if not isinstance(model_value, list) or not isinstance(advertised_value, list):
        return model_value
    narrowed = []
    for selector in model_value:
        if not isinstance(selector, dict):
            return model_value
        narrowed.append(_matching_advertised(selector, advertised_value) or selector)
    return narrowed


def build_resource_attributes(
    *,
    capability: ProviderCapability,
    turn: UserTurn,
    model_filled: dict,
    schema_context_index: dict[str, str],
    filterable: tuple[str, ...],
    declared: tuple[str, ...] = (),
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
    allowed = {path.split(".")[0].split("[")[0] for path in filterable}

    fallback: dict = {}
    location = _location_field(turn)
    # Only where the pack declares the field. Not "where it is filterable":
    # `AgricultureFacility` declares `location` and leaves it out of
    # `filterable_paths`, because it is the search origin rather than a filter
    # over advertised values — its own resource says to "invoke this Resource
    # with a fulfillment stop carrying a Point location". Gating on the filter
    # list dropped it, and a "krishi kendra near me" search had no point to
    # search around.
    #
    # MandiPrice declares none — it names `market.location`, the market's own
    # coordinates — so nothing is added there and the farmer's location reaches
    # the network as `/discover`'s spatial filter.
    if location is not None and "location" in declared:
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
    echoed = {
        field: value
        for field, value in capability.advertised.items()
        if field in allowed
    }
    narrowed = {
        field: _narrowed(value, echoed[field]) if field in echoed else value
        for field, value in model_filled.items()
    }
    return {**fallback, **echoed, **narrowed, **structural}
