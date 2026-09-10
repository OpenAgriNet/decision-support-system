"""Contract tests for the filesystem schema pack adapter.

No business-logic assertions here — only that the adapter reads the right
raw files and ignores the rest. Parsing/index behaviour is tier 1.
"""

from __future__ import annotations

from pathlib import Path

from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.core.provider_discovery.schema_fields import FieldSpec

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "network-specs" / "schema"


async def test_reads_the_three_files_a_pack_needs() -> None:
    source = FilesystemSchemaPackSource(root=FIXTURE_ROOT)

    packs = await source.fetch_packs()

    assert len(packs) == 1
    pack = packs[0]
    assert pack.pack_name == "MandiPrice"
    assert "openagrinet:MandiPrice" in pack.attributes_yaml
    assert '"version": "0.1.0"' in pack.profile_json
    assert len(pack.examples_json) == 1
    assert "Market" in pack.examples_json[0]


def _write_pack(root: Path, name: str, version: str = "v0.1") -> Path:
    """A minimal readable pack: a version dir with the two files a pack needs."""

    version_dir = root / name / version
    version_dir.mkdir(parents=True)
    (version_dir / "attributes.yaml").write_text(f"openagrinet:{name}\n")
    (version_dir / "profile.json").write_text('{"version": "0.1.0"}')
    return version_dir


_SHARED_ATTRIBUTES = """
components:
  schemas:
    AgricultureResourceFields:
      type: object
      required: [informationMode]
      properties:
        informationMode:
          type: string
          enum: [OnDemand, Direct]
"""

_PACK_ATTRIBUTES = """
components:
  schemas:
    Sample:
      type: object
      allOf:
        - $ref: "../../AgricultureResource/v0.1/attributes.yaml\
#/components/schemas/AgricultureResourceFields"
        - type: object
          properties:
            variety:
              type: string
"""


async def test_a_packs_fields_arrive_already_resolved(tmp_path: Path) -> None:
    """The adapter flattens while it still has paths.

    `flatten_fields` needs the file a pack's cross-file `$ref` points at, and
    only the reading layer knows where that is — `SchemaPackFiles` carries
    text, not paths. So resolving happens here, and downstream gets the fields
    without ever seeing the shared file.
    """

    shared = tmp_path / "AgricultureResource" / "v0.1"
    shared.mkdir(parents=True)
    (shared / "attributes.yaml").write_text(_SHARED_ATTRIBUTES)
    (shared / "profile.json").write_text("{}")

    pack = tmp_path / "Sample" / "v0.1"
    pack.mkdir(parents=True)
    (pack / "attributes.yaml").write_text(_PACK_ATTRIBUTES)
    (pack / "profile.json").write_text("{}")

    packs = await FilesystemSchemaPackSource(root=tmp_path).fetch_packs()

    sample = next(p for p in packs if p.pack_name == "Sample")
    # the inherited field and the pack's own, in one map
    assert sample.flattened_fields == {
        "informationMode": FieldSpec(
            type="string", required=True, enum=("OnDemand", "Direct")
        ),
        "variety": FieldSpec(type="string", required=False, enum=()),
    }


async def test_a_folder_that_is_not_a_pack_is_ignored(tmp_path: Path) -> None:
    """``schema/examples/`` sits alongside the packs in the real
    network-specs checkout: a directory of sample JSON, with no version dir
    and no ``attributes.yaml``. Treating it as a pack made the whole load
    raise, so *no* pack was read — one non-pack folder blinded every
    capability."""

    _write_pack(tmp_path, "MandiPrice")
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "live-mandi-price-resource.json").write_text("{}")

    packs = await FilesystemSchemaPackSource(root=tmp_path).fetch_packs()

    assert [pack.pack_name for pack in packs] == ["MandiPrice"]


async def test_a_pack_with_two_version_dirs_is_skipped_not_fatal(
    tmp_path: Path,
) -> None:
    """A genuinely malformed pack is different from a non-pack: it is
    reported, not silently ignored. But it still must not stop its siblings
    loading — ``index.py``'s own rule, "one bad pack can't blind every other
    capability", which the reading layer was breaking one level below where
    the skipping happened.

    Supersedes an earlier test that asserted this raised.
    """

    _write_pack(tmp_path, "MandiPrice")
    broken = tmp_path / "Broken"
    _write_pack(tmp_path, "Broken", version="v0.1")
    (broken / "v0.2").mkdir()

    source = FilesystemSchemaPackSource(root=tmp_path)
    packs = await source.fetch_packs()

    assert [pack.pack_name for pack in packs] == ["MandiPrice"]
    assert [skip.pack_name for skip in source.skipped] == ["Broken"]
    assert "exactly one version" in source.skipped[0].reason


async def test_a_pack_missing_profile_json_is_skipped(tmp_path: Path) -> None:
    """It looks like a pack — it has ``attributes.yaml`` — so it is reported
    rather than ignored."""

    _write_pack(tmp_path, "MandiPrice")
    version_dir = _write_pack(tmp_path, "Incomplete")
    (version_dir / "profile.json").unlink()

    source = FilesystemSchemaPackSource(root=tmp_path)
    packs = await source.fetch_packs()

    assert [pack.pack_name for pack in packs] == ["MandiPrice"]
    assert [skip.pack_name for skip in source.skipped] == ["Incomplete"]
