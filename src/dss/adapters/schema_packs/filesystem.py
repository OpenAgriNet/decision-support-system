"""Reads schema packs from a local network-specs checkout on disk."""

from __future__ import annotations

from pathlib import Path

import anyio.to_thread

from dss.core.provider_discovery.models import SchemaPackFiles


class FilesystemSchemaPackSource:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def fetch_packs(self) -> tuple[SchemaPackFiles, ...]:
        """Walking the network-specs tree is blocking disk I/O, so it runs on
        a worker thread — on the event loop it would stall every concurrent
        task for the length of the walk.
        """
        return await anyio.to_thread.run_sync(self._read_all_packs)

    def _read_all_packs(self) -> tuple[SchemaPackFiles, ...]:
        return tuple(
            self._read_pack(d) for d in sorted(self._root.iterdir()) if d.is_dir()
        )

    def _read_pack(self, pack_dir: Path) -> SchemaPackFiles:
        versions = [d for d in pack_dir.iterdir() if d.is_dir()]
        if len(versions) != 1:
            raise ValueError(
                f"expected exactly one version directory under {pack_dir}, "
                f"found {len(versions)}"
            )
        version_dir = versions[0]

        profile_json = (version_dir / "profile.json").read_text(encoding="utf-8")
        attributes_yaml = (version_dir / "attributes.yaml").read_text(encoding="utf-8")
        examples_dir = version_dir / "examples"
        examples_json = tuple(
            f.read_text(encoding="utf-8") for f in sorted(examples_dir.glob("*.json"))
        )

        return SchemaPackFiles(
            pack_name=pack_dir.name,
            version=version_dir.name,
            profile_json=profile_json,
            attributes_yaml=attributes_yaml,
            examples_json=examples_json,
        )
