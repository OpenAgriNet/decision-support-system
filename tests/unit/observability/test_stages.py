"""The stage vocabulary — a contract, not a convenience.

Span names and metric labels both derive from these values, so a dashboard
built on one breaks if the other drifts. ADR-0012 listed the names being bare
strings at call sites as a known weakness; this pins them.
"""

from dss.observability.stages import MODEL_BACKED_STAGES, Stage


def test_values_are_the_names_the_spans_already_use():
    # Renaming any of these renames a span and a metric label at once, which
    # is a breaking change for anything graphing them.
    assert {stage.value for stage in Stage} == {
        "intent",
        "enrichment",
        "moderation",
        "discovery",
        "planner",
        "composer",
    }


def test_a_stage_renders_as_its_own_name():
    assert f"dss.stage.{Stage.INTENT}" == "dss.stage.intent"


def test_only_the_four_agent_stages_are_model_backed():
    # Enrichment and discovery run no model, so `dss.stage.duration` carries no
    # `model` label for them.
    assert MODEL_BACKED_STAGES == frozenset(
        {Stage.INTENT, Stage.MODERATION, Stage.PLANNER, Stage.COMPOSER}
    )


def test_every_model_backed_stage_has_a_settings_field():
    # The label's value comes from `<stage>_model` in Settings. A stage named
    # here with no such setting would silently lose its model label.
    from dss.config.settings import Settings

    for stage in MODEL_BACKED_STAGES:
        assert f"{stage.value}_model" in Settings.model_fields
