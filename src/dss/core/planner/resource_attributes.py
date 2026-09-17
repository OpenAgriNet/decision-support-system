"""Assembles resourceAttributes for a /select call.

Structural fields (@context, @type, subjectCategories, location) come from the
schema pack, the ask and the turn — never from the model, and never from the
discover response. The model's own resource_attributes (a resolved commodity
code, topics, ...) merge on top, but cannot override a structural field: the
model chooses the capability by resource_id, not by rewriting @type after the
fact.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.models import UserTurn

# select is only called for an OnDemand capability — a Direct resource's
# values are already in the catalog, so there is nothing to select.
_ON_DEMAND = "OnDemand"

# TODO(#55): remove all three of these with the pack fix.
#
# Agmarknet refuses a MandiPrice select without a validity window —
# `SCH_INVALID_FORMAT: this capability needs a validity window; it reports
# prices over a date range`. The pack disagrees: `profile.json` lists
# `validity` under `result_fields` only, and `attributes.yaml` describes it as
# "the period during which a current price snapshot should be treated as
# applicable" — a property of the answer, not of the question. No pack lists
# `validity` as filterable, so this cannot come from the model: it would be
# refused by `validate_arguments`, which runs before this module.
#
# Only `profile.json` disagrees, though. `attributes.yaml` accepts the field
# on a select — a body carrying this window validates against the pack's own
# schema — so what is being worked around is the filterable list, not the
# contract. That is why the fix upstream is a one-line `filterable_paths`
# entry rather than a schema change.
#
# Scoped to MandiPrice deliberately. WeatherObservation also declares
# `validity` as a result field and answers correctly today without one, so
# sending it there would turn a working call into a NACK. When the pack
# settles what a caller may send, this goes and the field comes from
# `filterable_paths` like every other.
_VALIDITY_EXCEPTION_CAPABILITY = "openagrinet:MandiPrice"
# The provider publishes its own `validity` in IST, and a mandi's trading day
# is a local day rather than a UTC one — a UTC window would start and end at
# 05:30 local and straddle two trading days.
_MANDI_TZ = timezone(timedelta(hours=5, minutes=30))


def _validity_window(day: date) -> dict[str, str]:
    """One whole local day, as the `TimePeriod` the pack defines.

    `startsAt` and `endsAt` are both `date-time`, and `additionalProperties`
    is false — so these two keys, in this format, and nothing else.
    """

    return {
        "startsAt": datetime.combine(day, time.min, tzinfo=_MANDI_TZ).isoformat(),
        "endsAt": datetime.combine(day, time(23, 59, 59), tzinfo=_MANDI_TZ).isoformat(),
    }


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
    subject_category: str,
    turn: UserTurn,
    model_filled: dict,
    schema_context_index: dict[str, str],
    filterable: tuple[str, ...],
    declared: tuple[str, ...] = (),
    priced_on: date | None = None,
) -> dict:
    """Build the full resourceAttributes object: structural fields first, the
    model's fields merged on top — but structural fields always win.

    ``subject_category`` is the ask's own category, the same value ``/discover``
    filtered on, so both hops of one ask agree on what was asked.

    ``@context`` is the URL the pack declares, taken verbatim rather than
    rebuilt from a base URL and the pack's name and version.

    ``informationMode`` is constant here: ``select`` is only ever called for
    an OnDemand capability. A Direct resource's values are already in the
    catalog and arrive as a ``DiscoveredAnswer``, so there is nothing to
    select.
    """

    # `subjectCategories` states the ask, not the advertisement. Echoing the
    # resource's own categories back from the discover response said nothing
    # about what was wanted: a resource advertising `["Crop", "Practice"]`
    # returned both, and a scheme ask discovered on its category alone
    # (ADR-0009) selected such a resource as a crop question. The ask carries
    # exactly one category, so this is always a one-item list — and never the
    # empty one a resource with no advertised categories used to produce.
    structural: dict = {
        "@context": schema_context_index[capability.capability],
        "@type": capability.capability,
        "informationMode": _ON_DEMAND,
        "subjectCategories": [subject_category],
    }

    # Where the turn says the question is about. An OnDemand resource
    # advertises no `location` — there is no fixed point until someone asks —
    # so this is what names the place, for a forecast or a facility search
    # alike.
    allowed = {path.split(".")[0].split("[")[0] for path in filterable}

    turn_location: dict = {}
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
        turn_location["location"] = location

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
    # The turn's location last, after the model's own values. It wrote
    # `{"geo": "Nashik"}` — the place name where the schema requires a GeoJSON
    # geometry — over the point the district lookup had already resolved from
    # the farmer's words. The model cannot turn a name into coordinates, so
    # what it writes here can only be worse than what it replaces.
    #
    # The other packs work today because the model cannot reach their
    # location at all: `AgricultureFacility` declares it without listing it as
    # filterable, and MandiPrice's lives inside the echoed `market` object.
    # WeatherObservation is the one pack that offers `location.geo`, and the
    # one where this went wrong — so this is the rule the others already follow
    # by accident, made explicit.
    # TODO(#55): remove with the pack fix — see `_VALIDITY_EXCEPTION_CAPABILITY`.
    #
    # Last, like the turn's location, and for the same reason: what the pack
    # declares about `validity` describes an answer, so anything echoed or
    # model-filled under that name is not the window the provider is asking
    # for. `priced_on` is only absent in tests that predate this.
    #
    # The day the farmer asked about, which is today only by default — the
    # caller resolves "yesterday" or a named date before this. A window is
    # what the provider wants, so even one day arrives as a start and an end.
    validity: dict = {}
    if (
        capability.capability == _VALIDITY_EXCEPTION_CAPABILITY
        and priced_on is not None
    ):
        validity["validity"] = _validity_window(priced_on)

    return {**echoed, **narrowed, **structural, **turn_location, **validity}
