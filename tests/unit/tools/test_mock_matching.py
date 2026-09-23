"""Tier 1 — matching a /select request to a fake answer, in the mock network.

The speed benchmark needs one fake answer per question, not one per `@type`.
One answer per type would make the composer write "I don't have that" for
most questions, which skews its time. So the mock picks the answer from a few
key fields of the request.

Those fields are checked against what the mock advertised at discover. The
DSS echoes advertised values back at select, so they are stable across runs,
while the model's own wording is not.
"""

from __future__ import annotations

from tools.mock_network.matching import key_fields

MANDI = "openagrinet:MandiPrice"
WEATHER = "openagrinet:WeatherObservation"
ADVISORY = "openagrinet:KnowledgeAdvisory"

# One row per benchmark question. The words are what its topic must contain.
_ADVISORY_QUESTIONS = [
    {"question_id": "9-1", "match": ["stem borer", "wheat"]},
]

# One advertised resource per market, as a real mandi provider's discover
# returns them.
_ADVERTISED_MANDI = [
    {
        "market": {"marketCode": "1634", "marketName": "Rahuri APMC"},
        "supportedCommodities": [{"code": "23", "name": "Onion"}],
    },
]


def _mandi_request(*, commodity_code: str, market_code: str) -> dict:
    """The resourceAttributes of a real mandi /select, trimmed to what matters."""

    return {
        "@type": MANDI,
        "market": {
            "marketName": "Rahuri APMC",
            "state": "MH",
            "district": "338",
            "marketCode": market_code,
        },
        "supportedCommodities": [{"code": commodity_code, "name": "Onion"}],
        "informationMode": "OnDemand",
    }


def test_mandi_request_for_an_advertised_commodity_and_market_matches():
    request = _mandi_request(commodity_code="23", market_code="1634")

    assert key_fields(MANDI, request, _ADVERTISED_MANDI) == {
        "commodity": "23",
        "market": "1634",
    }


def test_mandi_request_for_a_commodity_not_advertised_matches_nothing():
    """A made-up answer here would be timed as if it were real. The miss has
    to show, so the turn can be counted and left out."""

    request = _mandi_request(commodity_code="99", market_code="1634")

    assert key_fields(MANDI, request, _ADVERTISED_MANDI) is None


def test_mandi_request_with_no_market_matches_nothing():
    """The mock's /select handler calls this. An uncaught error there becomes
    a 500, which the DSS retries as transient. So a missing field must come
    back as a miss, not an error."""

    request = _mandi_request(commodity_code="23", market_code="1634")
    del request["market"]

    assert key_fields(MANDI, request, _ADVERTISED_MANDI) is None


def test_weather_request_is_keyed_on_its_location():
    """The DSS turns the district in the question into a point from its
    district file, so the same question sends the same coordinates every run.
    Weather advertises a coverage area, not a list of places, so any located
    request matches."""

    request = {
        "@type": WEATHER,
        "location": {"geo": {"type": "Point", "coordinates": [74.5678, 16.8523]}},
    }

    assert key_fields(WEATHER, request, known=[]) == {
        "lon": 74.5678,
        "lat": 16.8523,
    }


def test_weather_request_with_no_location_matches_nothing():
    """This reaches the mock when the farmer names a district but the app
    sends no coordinates: the DSS uses the district's point for discover,
    not for select."""

    request = {"@type": WEATHER}

    assert key_fields(WEATHER, request, known=[]) is None


def test_advisory_topic_holding_every_match_word_names_its_question():
    """The planner writes `topics` itself, as a short phrase naming the
    subject and place from the question. Its case and wording around the
    key words may vary; the key words are what stays."""

    request = {"@type": ADVISORY, "topics": ["Stem borer on Wheat in Beed"]}

    assert key_fields(ADVISORY, request, _ADVISORY_QUESTIONS) == {"question_id": "9-1"}
