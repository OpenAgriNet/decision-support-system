"""Reads schema packs from a local network-specs checkout on disk.

Not every folder under ``schema/`` is a pack. The real checkout has an
``examples/`` sibling holding whole catalog payloads (``bppId``, ``provider``,
``resources``) — sample ``on_discover`` responses, not one capability's
attributes. It has no version directory, so treating it as a pack made the
entire load raise and *no* pack was read.

Those catalog samples are deliberately not loaded. ``build_capability_index``
infers a pack's categories from its ``examples_json``, and a catalog nests
``subjectCategories`` under ``resources[]`` rather than at the top — feeding
it one would leave the index silently wrong. The per-pack
``<pack>/<version>/examples/`` files, which *are* single resources, are the
ones the index reads.
"""

from __future__ import annotations

from pathlib import Path

import anyio.to_thread

from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped

# What makes a folder a pack: the file naming its @type. Without it there is
# no capability to index, so the folder is something else and is passed over.
# Chosen over profile.json because attributes.yaml carries the type constant
# the whole capability index is built from; profile.json only adds
# filterable_paths.
_PACK_MARKER = "attributes.yaml"

# A pack that looks like a pack but will not read. Skipped and reported, per
# index.py's rule: one bad pack in an external checkout must not blind every
# other capability.
_PACK_DEFECTS = (OSError, ValueError)


class FilesystemSchemaPackSource:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.skipped: tuple[SchemaPackSkipped, ...] = ()

    async def fetch_packs(self) -> tuple[SchemaPackFiles, ...]:
        """Walking the network-specs tree is blocking disk I/O, so it runs on
        a worker thread — on the event loop it would stall every concurrent
        task for the length of the walk.
        """
        return await anyio.to_thread.run_sync(self._read_all_packs)

    def _is_pack(self, folder: Path) -> bool:
        return any(folder.glob(f"*/{_PACK_MARKER}"))

    def _read_all_packs(self) -> tuple[SchemaPackFiles, ...]:
        packs: list[SchemaPackFiles] = []
        skipped: list[SchemaPackSkipped] = []
        for folder in sorted(self._root.iterdir()):
            if not folder.is_dir() or not self._is_pack(folder):
                continue
            try:
                packs.append(self._read_pack(folder))
            except _PACK_DEFECTS as exc:
                skipped.append(SchemaPackSkipped(folder.name, str(exc)))
        self.skipped = tuple(skipped)
        return tuple(packs)

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
