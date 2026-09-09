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
from dss.core.provider_discovery.models import SchemaPackFiles
from dss.entrypoint.composition import (
    _discovers_nothing,
    _network,
    _planner_schemas,
    build_runner,
)
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


def _pack_missing_x_jsonld() -> SchemaPackFiles:
    # No `x-jsonld` key — the shape that broke `provider_discovery.index`
    # against a real network-specs checkout (commit e1c4b82).
    return SchemaPackFiles(
        pack_name="Sample",
        version="v0.1",
        profile_json='{"filterable_paths": []}',
        attributes_yaml="""
components:
  schemas:
    Sample:
      type: object
""",
        examples_json=(),
    )


def test_a_pack_missing_its_x_jsonld_block_does_not_crash_the_boot() -> None:
    # `build_capability_index` already skips a pack like this rather than
    # raising (see provider_discovery/index.py's `PACK_DEFECTS`). The
    # planner's schema dict must degrade the same way, not take down
    # `build_runner` for every other capability over one bad pack.
    schemas = _planner_schemas((_pack_missing_x_jsonld(),))

    assert schemas == {}


def _pack_with_non_dict_profile() -> SchemaPackFiles:
    # `profile.json` parses to a list, not an object — valid JSON, wrong
    # shape. `parse_domain_schema` indexes it with a string key and raises
    # `TypeError`, which is one of `PACK_DEFECTS` but was not one of the
    # exceptions this loop used to catch.
    return SchemaPackFiles(
        pack_name="Sample",
        version="v0.1",
        profile_json="[]",
        attributes_yaml="""
components:
  schemas:
    Sample:
      x-jsonld:
        "@type": openagrinet:Sample
""",
        examples_json=(),
    )


def test_a_pack_with_a_non_dict_profile_does_not_crash_the_boot() -> None:
    schemas = _planner_schemas((_pack_with_non_dict_profile(),))

    assert schemas == {}


def _write_good_pack(root: Path) -> None:
    pack_dir = root / "MandiPrice" / "v0.1"
    pack_dir.mkdir(parents=True)
    (pack_dir / "attributes.yaml").write_text(
        """
components:
  schemas:
    MandiPrice:
      x-jsonld:
        "@context": "https://schemas.openagrinet.global/schema/MandiPrice/v0.1/context.jsonld"
        "@type": openagrinet:MandiPrice
""",
        encoding="utf-8",
    )
    (pack_dir / "profile.json").write_text("{}", encoding="utf-8")
    (pack_dir / "examples").mkdir()


def _write_malformed_pack(root: Path) -> None:
    # Two version directories under one pack — `FilesystemSchemaPackSource`
    # itself refuses to pick one, and skips the pack (see its `_PACK_DEFECTS`).
    pack_dir = root / "Broken"
    (pack_dir / "v0.1").mkdir(parents=True)
    (pack_dir / "v0.1" / "attributes.yaml").write_text(
        "components: {}", encoding="utf-8"
    )
    (pack_dir / "v0.2").mkdir(parents=True)
    (pack_dir / "v0.2" / "attributes.yaml").write_text(
        "components: {}", encoding="utf-8"
    )


def test_a_skipped_pack_is_logged_for_an_operator_to_see(
    tmp_path: Path, caplog
) -> None:
    schema_root = tmp_path / "schema"
    _write_good_pack(schema_root)
    _write_malformed_pack(schema_root)
    settings = _settings(
        tmp_path,
        discovery_base_url="https://discovery.example/oan",
        invocation_base_url="https://select.example/oan",
        schema_pack_dir=schema_root,
    )

    with caplog.at_level("WARNING"):
        _network(settings)

    # `SchemaPackSkipped.pack_name` and `.reason` both reach the log — an
    # operator reading it can tell which pack and why, not just "something
    # was skipped" (see `SchemaPackSkipped`'s docstring: "has to reach an
    # operator rather than pass silently").
    assert any("Broken" in record.message for record in caplog.records)
