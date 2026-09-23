"""Tier 1 — the turn body the benchmark sends is one the DSS accepts.

Checked against the DSS's own request schema, not a copy of it: the stand-in
server in the tier-2 test accepts any body, so a field the DSS renamed would
pass there and fail every real turn with a 422.
"""

from __future__ import annotations

from dss.adapters.http.v1.schema import TurnRequest
from evals.perf.questions import Question
from evals.perf.turn import turn_request

AKOLA = Question(
    id="37-1",
    category="weather",
    text="Will it rain tomorrow in Akola district?",
    region="IN-MH",
    area="Akola",
    point=(77.056016, 20.748005),
)


def test_the_body_passes_the_dss_request_schema_and_carries_the_point():
    body = turn_request(AKOLA, session_id="bench_s1", transaction_id="bench_t1")

    parsed = TurnRequest.model_validate(body)

    location = parsed.message.attributes.location
    assert tuple(location.geometry.coordinates) == AKOLA.point
    assert parsed.message.input[-1].content[0].text == AKOLA.text
