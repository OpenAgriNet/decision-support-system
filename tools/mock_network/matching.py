"""Which fake answer a /select request is asking for.

Matched on a few key fields, not the whole request: the planner is a model,
so it may phrase the same ask slightly differently each run. Each type keys on
the fields that stay the same across runs.
"""

from __future__ import annotations

from collections.abc import Callable


def _mandi_key(attrs: dict, known: list[dict]) -> dict | None:
    """Keyed on the commodity and market, both advertised values the DSS
    echoes back. A pair no advertised resource offers is a miss."""

    commodity = attrs["supportedCommodities"][0]["code"]
    market = attrs["market"]["marketCode"]
    if not any(
        resource["market"]["marketCode"] == market
        and commodity in {c["code"] for c in resource["supportedCommodities"]}
        for resource in known
    ):
        return None
    return {"commodity": commodity, "market": market}


def _weather_key(attrs: dict, known: list[dict]) -> dict | None:
    """Keyed on the point. Weather advertises a coverage area, not a list of
    places, so any located request matches."""

    lon, lat = attrs["location"]["geo"]["coordinates"]
    return {"lon": lon, "lat": lat}


def _advisory_key(attrs: dict, known: list[dict]) -> dict | None:
    """Keyed on the question. The planner writes `topics` as a short phrase
    from the question, so `known` holds each question's match words."""

    topics = [topic.lower() for topic in attrs["topics"]]
    for row in known:
        if any(all(word in topic for word in row["match"]) for topic in topics):
            return {"question_id": row["question_id"]}
    return None


_KEY_BY_TYPE: dict[str, Callable[[dict, list[dict]], dict | None]] = {
    "openagrinet:MandiPrice": _mandi_key,
    "openagrinet:WeatherObservation": _weather_key,
    "openagrinet:KnowledgeAdvisory": _advisory_key,
}


def key_fields(capability: str, attrs: dict, known: list[dict]) -> dict | None:
    """The key a fake answer is built from, or None when nothing matches.

    `known` is what the mock can answer for this capability: the advertised
    resources for mandi, the question rows for advisory.
    """

    key_for = _KEY_BY_TYPE.get(capability)
    if key_for is None:
        return None
    # A malformed request is a miss, not an error: an uncaught error becomes a
    # 500, which the DSS retries as transient.
    try:
        return key_for(attrs, known)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
