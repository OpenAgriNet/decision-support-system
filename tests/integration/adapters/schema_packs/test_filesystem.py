"""Contract tests for the filesystem schema pack adapter.

No business-logic assertions here — only that the adapter reads the right
raw files and ignores the rest. Parsing/index behaviour is tier 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "network-specs" / "schema"


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_a_pack_with_two_version_dirs_fails_loudly(tmp_path: Path) -> None:
    pack_dir = tmp_path / "MandiPrice"
    (pack_dir / "v0.1").mkdir(parents=True)
    (pack_dir / "v0.2").mkdir(parents=True)
    source = FilesystemSchemaPackSource(root=tmp_path)

    with pytest.raises(ValueError, match="exactly one version"):
        await source.fetch_packs()
