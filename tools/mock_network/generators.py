"""Fake /select answers for the speed benchmark.

The benchmark times the composer, whose time grows with how much it has to
read. So each answer is as big as a real one, and the same on every run.
"""

from __future__ import annotations

import random
from collections.abc import Callable

# The pack's `parameters[].parameter` enum (WeatherObservation/v0.1). All of
# them, because the full list is the largest answer a real provider could send.
# Each maps to its unit and its values, drawn from a range a real forecast
# for an Indian district could show.
_WEATHER_PARAMETERS: dict[str, tuple[str, Callable[[random.Random], dict]]] = {
    "Rainfall": ("mm", lambda r: {"sum": round(r.uniform(0, 50), 1)}),
    "Temperature": (
        "Cel",
        lambda r: {
            "minimum": round(r.uniform(15, 25), 1),
            "maximum": round(r.uniform(26, 40), 1),
        },
    ),
    "Humidity": ("%", lambda r: {"mean": round(r.uniform(30, 95))}),
    "WindSpeed": ("km/h", lambda r: {"mean": round(r.uniform(0, 30), 1)}),
    "WindDirection": ("deg", lambda r: {"instantaneous": r.randrange(360)}),
    "SoilMoisture": ("%", lambda r: {"mean": round(r.uniform(10, 45))}),
    "Evapotranspiration": ("mm", lambda r: {"sum": round(r.uniform(2, 8), 1)}),
    "Alert": ("1", lambda r: {"instantaneous": r.choice(["None", "Heavy rain"])}),
}


def advisory_answer(text: str) -> dict:
    """The resourceAttributes of an advisory answer carrying `text`.

    `text` is the hand-written answer for one benchmark question.
    """

    return {
        "@type": "openagrinet:KnowledgeAdvisory",
        "informationMode": "Direct",
        "subjectCategories": ["Crop"],
        "recommendations": [{"message": text}],
    }


def weather_answer(lon: float, lat: float) -> dict:
    """The resourceAttributes of a weather answer for the point.

    Seeded from the point, not the global `random`: each district gets its
    own numbers, and the same numbers every run, so two runs compare.
    """

    rng = random.Random(f"{lon},{lat}")
    return {
        "@type": "openagrinet:WeatherObservation",
        "informationMode": "Direct",
        "subjectCategories": ["Weather"],
        "parameters": [
            {"parameter": name, "values": values(rng), "unit": unit}
            for name, (unit, values) in _WEATHER_PARAMETERS.items()
        ],
    }


def mandi_answer(commodity: dict, market: dict) -> dict:
    """The resourceAttributes of a mandi answer: one price for one commodity
    at one market, as a real provider answers.

    Seeded from the commodity and market, for the same reason as weather.
    """

    rng = random.Random(f"{commodity['code']},{market['marketCode']}")
    modal = rng.randrange(1000, 6000)
    return {
        "@type": "openagrinet:MandiPrice",
        "informationMode": "Direct",
        "subjectCategories": ["Market"],
        "supportedCommodities": [commodity],
        "market": market,
        "prices": {
            "minimum": modal - rng.randrange(0, 800),
            "maximum": modal + rng.randrange(0, 3000),
            "modal": modal,
            "currency": "INR",
            "unit": "Rs./Qtl",
        },
    }
