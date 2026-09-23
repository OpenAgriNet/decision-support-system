"""Tier 1 — building a fake /select answer, in the mock network.

The speed benchmark times how long the composer takes to write its answer,
and that grows with how much it has to read. So the fake answers are real in
size, and built the same way every run so two runs can be compared.
"""

from __future__ import annotations

import pytest

from dss.config.schema_pack_fetch import DEFAULT_PACK_DIR
from dss.core.provider_discovery.schema_fields import FieldSpec, flatten_fields
from tools.mock_network.generators import (
    advisory_answer,
    mandi_answer,
    weather_answer,
)
from tools.mock_network.validation import check_against_pack


def _pack_fields(pack: str) -> dict[str, FieldSpec]:
    """The real pack's fields, as fetched into `var/schema-packs`."""

    attributes = DEFAULT_PACK_DIR / pack / "v0.1" / "attributes.yaml"
    if not attributes.exists():
        pytest.skip(f"no schema packs at {DEFAULT_PACK_DIR} — run run-local.sh once")
    shared = DEFAULT_PACK_DIR / "AgricultureResource" / "v0.1" / "attributes.yaml"
    return flatten_fields(
        attributes.read_text(), pack_name=pack, shared_yaml=shared.read_text()
    )


def test_advisory_answer_carries_the_written_answer_word_for_word():
    """Advisory answers are written by hand, one per benchmark question, so
    the composer reads advice that fits what was asked."""

    text = "For stem borer on wheat, remove dead hearts early."

    attrs = advisory_answer(text)

    assert attrs["recommendations"][0]["message"] == text


def test_advisory_answer_is_a_body_the_real_pack_accepts():
    """The mock writes these bodies itself, so it can leave out a field the
    pack requires or invent one it never declares. A real provider's answer
    could not do either."""

    attrs = advisory_answer("Remove dead hearts early.")

    check_against_pack(attrs, fields=_pack_fields("KnowledgeAdvisory"))


# The pack's `parameters[].parameter` enum, from
# WeatherObservation/v0.1/attributes.yaml. Named here because the flattened
# fields do not carry a nested item's enum.
_WEATHER_PARAMETERS = {
    "Rainfall",
    "Temperature",
    "Humidity",
    "WindSpeed",
    "WindDirection",
    "SoilMoisture",
    "Evapotranspiration",
    "Alert",
}


def test_weather_answer_carries_every_parameter_the_pack_names():
    """Every parameter the pack allows is the largest answer a real provider
    could send, so the composer reads the most it ever would."""

    attrs = weather_answer(lon=77.0, lat=20.7)

    assert {p["parameter"] for p in attrs["parameters"]} == _WEATHER_PARAMETERS


def _values(attrs: dict) -> list[dict]:
    return [p["values"] for p in attrs["parameters"]]


def test_weather_answers_for_two_points_differ():
    """Each district gets its own forecast, so the composer is not reading
    the same numbers for every question."""

    akola = weather_answer(lon=77.0, lat=20.7)
    sangli = weather_answer(lon=74.5, lat=16.8)

    assert _values(akola) != _values(sangli)


def test_weather_answer_for_a_point_is_the_same_every_run():
    """Runs are compared on tokens as well as time (#164), so the same
    question must send the composer the same numbers every run."""

    assert weather_answer(lon=77.0, lat=20.7) == weather_answer(lon=77.0, lat=20.7)


def test_weather_answer_is_a_body_the_real_pack_accepts():
    attrs = weather_answer(lon=77.0, lat=20.7)

    check_against_pack(attrs, fields=_pack_fields("WeatherObservation"))


# What the mock advertised for one market, as a real mandi discover sends it.
_ONION = {"code": "23", "name": "Onion"}
_RAHURI = {
    "marketCode": "1634",
    "marketName": "Rahuri APMC",
    "district": "Ahilyanagar",
    "state": "Maharashtra",
}


def test_mandi_answer_is_a_body_the_real_pack_accepts():
    """A real mandi answer is one price for one commodity at one market. The
    pack requires the commodity and market it is for, so both are echoed."""

    attrs = mandi_answer(commodity=_ONION, market=_RAHURI)

    check_against_pack(attrs, fields=_pack_fields("MandiPrice"))


def test_mandi_answer_carries_one_price_that_makes_sense():
    """A price the composer would quote as nonsense (a modal above the
    maximum) would make it hedge, which is not how a real answer reads."""

    prices = mandi_answer(commodity=_ONION, market=_RAHURI)["prices"]

    assert prices["minimum"] <= prices["modal"] <= prices["maximum"]
    assert (prices["currency"], prices["unit"]) == ("INR", "Rs./Qtl")
