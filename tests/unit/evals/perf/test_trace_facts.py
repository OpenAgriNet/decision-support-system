"""Tier 1 — reading one turn's trace into the figures the benchmark reports.

The fixture is a real turn's trace from Langfuse v4's observations API (Akola
weather, 2026-09-23), trimmed to the fields read here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.perf.traces import TokenCall, to_trace_facts

_TRACE = Path(__file__).parent / "fixtures" / "langfuse_trace.json"


def _observations() -> list[dict]:
    return json.loads(_TRACE.read_text())["data"]


def test_each_stage_and_provider_call_has_its_time():
    facts = to_trace_facts(_observations())

    assert facts.stages == pytest.approx(
        {
            "intent": 1.120,
            "moderation": 0.615,
            "enrichment": 0.0,
            "discovery": 0.012,
            "planner": 2.129,
            "composer": 0.535,
            "discover": 0.010,
            "select": 0.004,
        },
        abs=0.001,
    )


def test_calls_that_overlap_count_once_as_the_wait_they_cause():
    """A two-category question sends two /discover calls at once. Summed,
    the figure would be about twice the real wait, and longer than the
    stage that holds them. First start to last end is what the turn waited."""

    observations = _observations()
    discover = next(o for o in observations if o["name"] == "dss.discover")
    # A second call that starts 2 ms into the first and ends 5 ms after it.
    observations.append(
        {
            **discover,
            "id": "second-discover",
            "startTime": "2026-09-23T13:57:55.003Z",
            "endTime": "2026-09-23T13:57:55.016Z",
        }
    )
    discover["startTime"] = "2026-09-23T13:57:55.001Z"
    discover["endTime"] = "2026-09-23T13:57:55.011Z"

    facts = to_trace_facts(observations)

    assert facts.stages["discover"] == pytest.approx(0.015, abs=0.0005)


def test_calls_apart_in_time_do_not_count_the_gap_between_them():
    """The planner can /select, think, then /select again. The thinking is
    the planner's time, not the network's."""

    observations = _observations()
    select = next(o for o in observations if o["name"] == "dss.select")
    select["startTime"] = "2026-09-23T13:57:55.000Z"
    select["endTime"] = "2026-09-23T13:57:55.010Z"
    observations.append(
        {
            **select,
            "id": "second-select",
            "startTime": "2026-09-23T13:57:57.000Z",
            "endTime": "2026-09-23T13:57:57.010Z",
        }
    )

    assert to_trace_facts(observations).stages["select"] == pytest.approx(
        0.020, abs=0.0005
    )


def test_tokens_come_from_each_model_call_under_the_agent_that_made_it():
    """Only the `chat` generations: an agent's own span carries the sum of
    its calls, so counting both would count every token twice."""

    facts = to_trace_facts(_observations())

    assert sorted(facts.calls, key=lambda c: (c.agent, c.input)) == [
        TokenCall(agent="composer", input=775, output=17),
        TokenCall(agent="intent-classifier", input=925, output=52),
        TokenCall(agent="moderator", input=358, output=23),
        TokenCall(agent="planner", input=1236, output=15),
        TokenCall(agent="planner", input=1423, output=72),
        TokenCall(agent="planner", input=1748, output=11),
    ]


def test_the_models_are_the_ones_the_server_ran_with():
    """Read from the turn's own span, not the client's settings: the server
    is what actually ran, so a stale local setting cannot mislabel a result."""

    facts = to_trace_facts(_observations())

    assert facts.models == {
        "intent": "openai:gemma-4-31b-it",
        "moderation": "openai:gemma-4-31b-it",
        "planner": "openai:gemma-4-31b-it",
        "composer": "openai:gemma-4-31b-it",
    }


def test_a_real_trace_hangs_its_stages_off_the_turn():
    assert to_trace_facts(_observations()).flat is False


def test_a_stage_not_under_the_turn_marks_the_trace_flat():
    """If the context did not carry across a task boundary, a stage becomes a
    root of its own. Its time then says nothing about this turn, so the
    benchmark leaves stage figures out rather than trust them."""

    observations = _observations()
    planner = next(o for o in observations if o["name"] == "dss.stage.planner")
    planner["parentObservationId"] = None

    assert to_trace_facts(observations).flat is True
