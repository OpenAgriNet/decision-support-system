"""The labels a metric may carry.

Every distinct combination of label values is its own time series. The labels
here are bounded — six stages, a handful of models, six statuses, one profile
per deployment. An unbounded one (a provider id, a district, a farmer id) would
multiply series until the backend degrades, and that failure appears in the
backend months later rather than in a test. So it is pinned here instead.

Also §6.1: a farmer's words may not reach telemetry, and a label is telemetry.
"""

import pytest

from dss.adapters.observability.metrics import LABEL_KEYS

# Anything shaped like one of these has no bounded set of values, identifies a
# person or a provider, or is a location. None may ever become a label.
FORBIDDEN = (
    "query",
    "question",
    "answer",
    "text",
    "message",
    "user",
    "farmer",
    "phone",
    "provider",
    "resource",
    "capability",
    "district",
    "region",
    "area",
    "location",
    "geometry",
    "lat",
    "lon",
    "transaction",
    "session",
)


def test_the_instruments_are_the_ones_the_dashboard_expects():
    assert set(LABEL_KEYS) == {
        "dss.turn.duration",
        "dss.turn.first_claim.duration",
        "dss.stage.duration",
        "dss.turn.count",
        "dss.stage.tokens",
        "dss.turn.cost",
    }


@pytest.mark.parametrize(
    ("instrument", "labels"),
    [
        ("dss.turn.duration", {"status", "model_profile"}),
        ("dss.turn.first_claim.duration", {"model_profile"}),
        ("dss.stage.duration", {"stage", "model"}),
        ("dss.turn.count", {"status", "model_profile"}),
        ("dss.stage.tokens", {"stage", "model", "direction"}),
        ("dss.turn.cost", {"model_profile"}),
    ],
)
def test_each_instrument_carries_exactly_these_labels(instrument, labels):
    assert set(LABEL_KEYS[instrument]) == labels


def test_no_label_could_carry_a_farmer_value_a_provider_or_a_location():
    for instrument, labels in LABEL_KEYS.items():
        for label in labels:
            for forbidden in FORBIDDEN:
                assert forbidden not in label, (
                    f"{instrument} has a label {label!r} containing {forbidden!r}. "
                    "Unbounded labels degrade the metrics backend, and a "
                    "farmer-supplied value in one breaks §6.1."
                )
