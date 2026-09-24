"""Tier 1 — turning a run's turns into the reported figures."""

from __future__ import annotations

import json

from evals.perf.report import figures, render_text, write_json
from evals.perf.runner import RunResult, TurnResult
from evals.perf.traces import TokenCall, TraceFacts
from evals.perf.turn import TurnTiming


def _turn(
    first: float | None,
    total: float,
    *,
    status: str = "answered",
    missed: bool = False,
    facts: TraceFacts | None = None,
) -> TurnResult:
    return TurnResult(
        question_id="1-1",
        category="mandi",
        repeat=1,
        session_id="s",
        timing=TurnTiming(status=status, first_delta_s=first, total_s=total),
        missed=missed,
        facts=facts,
    )


def test_a_missed_turn_is_counted_but_left_out_of_the_figures():
    result = RunResult(
        turns=[_turn(1.0, 2.0), _turn(9.0, 9.0, missed=True), _turn(3.0, 4.0)]
    )

    report = figures(result)

    # "sent", not "timed": the missed turn was sent but is not in the figures.
    assert report["turns"]["sent"] == 3
    assert report["turns"]["missed"] == 1
    assert report["first_delta_s"]["max"] == 3.0
    assert report["total_s"]["n"] == 2


def test_turns_are_counted_by_how_they_ended():
    """A turn that asked for a district streams no answer. Counting how many
    ended each way shows when the figures rest on fewer turns than sent."""

    result = RunResult(
        turns=[
            _turn(1.0, 2.0),
            _turn(None, 1.0, status="requires_input"),
            _turn(1.5, 2.5),
        ]
    )

    assert figures(result)["turns"]["by_status"] == {
        "answered": 2,
        "requires_input": 1,
    }


def _facts(planner: float, *, flat: bool = False, calls=()) -> TraceFacts:
    return TraceFacts(
        stages={"planner": planner}, calls=list(calls), models={}, flat=flat
    )


def test_stage_times_come_only_from_traces_that_hang_off_the_turn():
    """A flat trace's stage times say nothing about its turn."""

    result = RunResult(
        turns=[
            _turn(1.0, 2.0, facts=_facts(2.0)),
            _turn(1.0, 2.0, facts=_facts(9.0, flat=True)),
            _turn(1.0, 2.0, facts=None),
            _turn(1.0, 2.0, facts=_facts(3.0)),
        ]
    )

    report = figures(result)

    assert report["stages_s"]["planner"]["max"] == 3.0
    assert report["stages_s"]["planner"]["n"] == 2
    assert report["turns"]["flat_traces"] == 1
    assert report["turns"]["no_trace"] == 1


def test_tokens_are_summed_up_per_model_call_for_each_agent():
    """Per call, not per turn: the planner makes a varying number of calls,
    and a per-turn sum would hide whether each call grew or there were more
    of them."""

    result = RunResult(
        turns=[
            _turn(
                1.0,
                2.0,
                facts=_facts(
                    1.0,
                    calls=[
                        TokenCall(agent="planner", input=1000, output=10),
                        TokenCall(agent="planner", input=1400, output=70),
                        TokenCall(agent="composer", input=800, output=200),
                    ],
                ),
            )
        ]
    )

    tokens = figures(result)["tokens_per_call"]

    assert tokens["planner"]["input"]["max"] == 1400
    assert tokens["planner"]["input"]["n"] == 2
    assert tokens["composer"]["output"]["p50"] == 200


def test_the_run_records_its_models_commit_machine_and_whether_it_was_cut_short():
    """A figure means nothing without what produced it: two runs compare only
    on the same models, the same machine, and a full set of turns."""

    models = {"planner": "openai:gemma-4-31b-it"}
    facts = TraceFacts(stages={}, calls=[], models=models, flat=False)
    result = RunResult(turns=[_turn(1.0, 2.0, facts=facts)], truncated=True)

    run = figures(result, commit="abc1234", machine={"cpu_count": 2})["run"]

    assert run == {
        "models": models,
        "commit": "abc1234",
        "machine": {"cpu_count": 2},
        "truncated": True,
    }


def test_the_printed_report_shows_each_figure_and_what_produced_it():
    facts = _facts(2.0, calls=[TokenCall(agent="planner", input=1000, output=10)])
    result = RunResult(turns=[_turn(1.25, 2.5, facts=facts)], truncated=True)

    text = render_text(
        figures(result, commit="abc1234", machine={"processor": "test-cpu"})
    )

    assert "first piece" in text and "1.25" in text
    assert "total" in text and "2.50" in text
    assert "planner" in text and "1000" in text
    assert "abc1234" in text and "test-cpu" in text
    assert "stopped the run early" in text
    assert "same machine" in text


def test_stages_print_in_pipeline_order_with_network_calls_under_their_stage():
    """The trace lists spans in no useful order, and `discover` beside
    `discovery` reads as one thing twice."""

    stages = {
        "composer": 0.5,
        "select": 0.01,
        "planner": 2.0,
        "discover": 0.01,
        "discovery": 0.02,
        "intent": 1.0,
    }
    facts = TraceFacts(stages=stages, calls=[], models={}, flat=False)

    text = render_text(figures(RunResult(turns=[_turn(1.0, 2.0, facts=facts)])))

    in_order = [
        "  intent ",
        "  discovery ",
        "    ↳ network /discover",
        "  planner ",
        "    ↳ network /select",
        "  composer ",
    ]
    positions = [text.index(line) for line in in_order]
    assert positions == sorted(positions)


def test_a_stage_the_report_does_not_know_still_prints():
    """A stage added to the DSS later must show up, not vanish silently."""

    facts = TraceFacts(stages={"review": 0.3}, calls=[], models={}, flat=False)

    text = render_text(figures(RunResult(turns=[_turn(1.0, 2.0, facts=facts)])))

    assert "  review " in text


def test_the_footer_is_short_where_the_json_is_whole():
    """One model line when all four agents share it, a 12-character commit,
    and the machine on one line. The JSON keeps every field in full."""

    same = dict.fromkeys(("intent", "moderation", "planner", "composer"), "m:x")
    facts = TraceFacts(stages={}, calls=[], models=same, flat=False)
    # Made-up values, handed in: the report formats what it is given and never
    # reads the machine it runs on, so this passes on any CI runner.
    machine = {
        "platform": "test-os",
        "processor": "test-cpu",
        "cpu_count": 2,
        "python": "3.0",
    }

    text = render_text(
        figures(
            RunResult(turns=[_turn(1.0, 2.0, facts=facts)]),
            commit="0123456789abcdef0123456789abcdef01234567-dirty",
            machine=machine,
        )
    )

    assert "models: m:x (all four agents)" in text
    assert "commit: 0123456789ab-dirty" in text
    assert "machine: test-os · test-cpu · 2 cores · Python 3.0" in text


def test_different_models_are_listed_per_agent():
    models = {"planner": "m:big", "composer": "m:small"}
    facts = TraceFacts(stages={}, calls=[], models=models, flat=False)

    text = render_text(figures(RunResult(turns=[_turn(1.0, 2.0, facts=facts)])))

    assert "models: planner=m:big, composer=m:small" in text


def test_the_json_file_holds_the_figures_and_one_row_per_turn(tmp_path):
    """The raw rows let the figures be recomputed, or a slow turn be found by
    its session in Langfuse."""

    result = RunResult(turns=[_turn(1.0, 2.0), _turn(9.0, 9.0, missed=True)])
    report = figures(result, commit="abc1234")

    path = write_json(report, result, tmp_path)

    written = json.loads(path.read_text())
    assert path.name.endswith("-abc1234.json")
    assert written["total_s"] == report["total_s"]
    assert [row["missed"] for row in written["turn_rows"]] == [False, True]
    assert written["turn_rows"][0]["session_id"] == "s"
