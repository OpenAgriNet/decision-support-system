"""Tier 3 — the composition root wires the live orchestrator.

No network and no model keys: the LLM ports are stubbed and the discovery /
invocation adapters are exercised only for *construction*, never a call. These
tests pin the seam the design leans on — supplying the three network settings
swaps the unwired discovery for the real client — and that the wired branch
assembles the planner's schema dicts correctly from real schema packs.
"""

from __future__ import annotations

from pathlib import Path

from dss.config.settings import Settings
from dss.entrypoint.composition import _discovers_nothing, _network, build_runner
from dss.orchestration.orchestrator import Orchestrator
from dss.ports.turn import TurnRunner

SCHEMA_PACKS_FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "adapters"
    / "schema_packs"
    / "fixtures"
    / "network-specs"
    / "schema"
)


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(stub_llm=True, evidence_dir=tmp_path, **overrides)


def test_build_runner_returns_a_turn_runner(tmp_path: Path) -> None:
    runner = build_runner(_settings(tmp_path))

    assert isinstance(runner, Orchestrator)
    assert isinstance(runner, TurnRunner)  # satisfies the driving port


def test_unwired_network_discovers_nothing(tmp_path: Path) -> None:
    discover, _invocation, schemas, schema_context_index = _network(_settings(tmp_path))

    # the seam is the single unwired function, and the planner's dicts are empty
    assert discover is _discovers_nothing
    assert schemas == {} and schema_context_index == {}


def test_wired_network_builds_the_planner_schemas(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        discovery_base_url="https://discovery.example/oan",
        invocation_base_url="https://select.example/oan",
        schema_pack_dir=SCHEMA_PACKS_FIXTURE_ROOT,
    )
    assert settings.network_enabled

    discover, _invocation, schemas, schema_context_index = _network(settings)

    # the real client replaced the unwired stand-in...
    assert discover is not _discovers_nothing
    # ...the schema packs loaded: the context index is keyed by the advertised
    # @type, which differs from the "MandiPrice" pack folder name.
    assert "openagrinet:MandiPrice" in schema_context_index
    # ...and the planner's filter schemas are keyed the same way. The fixture is
    # a Direct-answer pack with no `filterable_paths`, so it is (correctly) left
    # out of `schemas` while still resolvable through the context index.
    assert set(schemas) <= set(schema_context_index)
