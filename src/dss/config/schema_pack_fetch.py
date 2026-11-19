"""Fetches network-specs schema packs onto the filesystem.

The packs are an external checkout: they live in the `OpenAgriNet/network-specs`
repo, not this one, and nothing clones them. This module pulls the ones the DSS
reads onto disk, where `FilesystemSchemaPackSource` reads them.

`urllib.request` rather than `httpx2`, deliberately: this runs before the app
is built — and from `scripts/fetch_schema_packs.py`, which a fresh clone may
run before `uv sync` — so it must not need a third-party package.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

_REPO = "OpenAgriNet/network-specs"
_PAGES_HOST = "https://openagrinet.github.io"
_API_HOST = "https://api.github.com"

# The only version published today. When v0.2 lands this becomes a per-pack
# lookup, not a constant.
_VERSION = "v0.1"

# The branch the published site is built from. Pages cannot list a
# directory, so the Contents API lists the examples on this branch instead.
_PUBLISHED_BRANCH = "main"

# Where the packs go. Anchored to this file, not the working directory: the
# service is started with `uvicorn --factory` from wherever the operator
# happens to be, and a relative default would resolve against that.
#
# `parents[3]` is the repo root from `src/dss/config/`. That holds for a source
# checkout, which is what the default is for. An installed wheel puts this
# module under `site-packages`, where there is no repo root and no
# `var/` — a deployment sets DSS_SCHEMA_PACK_DIR to a mounted path instead.
DEFAULT_PACK_DIR = Path(__file__).parents[3] / "var" / "schema-packs"

# The packs the DSS reads.
#
# AgricultureResource is not routed to directly — the other catalog packs `$ref` it
# with a relative path (`../../AgricultureResource/v0.1/attributes.yaml`), so
# it has to be on disk beside them. Nothing resolves those refs today
# (`yaml.safe_load` treats `$ref` as a plain string key), so it looks unused;
# it is not. Do not drop it.
PACKS = (
    "AgricultureResource",
    "MandiPrice",
    "WeatherObservation",
    "KnowledgeAdvisory",
    "AgricultureFacility",
    # An API pack: forms are built from it (#205). Its `profile.json` says so.
    "Grievance",
)

# What the loader needs. Absent either, the pack is not published.
_ESSENTIAL_FILES = ("attributes.yaml", "profile.json")

# Fetched so the tree mirrors upstream, though nothing reads them today. A 404
# on one is not a reason to drop the pack.
_OPTIONAL_FILES = ("context.jsonld", "vocab.jsonld", "renderer.json", "README.md")

_TIMEOUT_SECONDS = 30


class SchemaPackFetchFailedError(RuntimeError):
    """The fetch produced nothing usable. Never raised for a partial result."""


def pack_file_url(*, pack: str, filename: str, host: str = _PAGES_HOST) -> str:
    return f"{host}/network-specs/schema/{pack}/{_VERSION}/{filename}"


def examples_api_url(*, pack: str, host: str = _API_HOST) -> str:
    return (
        f"{host}/repos/{_REPO}/contents/schema/{pack}/{_VERSION}/examples"
        f"?ref={_PUBLISHED_BRANCH}"
    )


def fetch_packs(
    *,
    dest: Path,
    packs: tuple[str, ...] = PACKS,
    pages_host: str = _PAGES_HOST,
    api_host: str = _API_HOST,
) -> tuple[str, ...]:
    """Download the packs onto disk under `dest`, returning what was written.

    Each pack lands as `<dest>/<Pack>/<version>/`, the layout
    `FilesystemSchemaPackSource` walks.

    A pack is written only once all of its files are in hand: a half-written
    pack is worse than an absent one, because the loader catches `OSError` and
    silently skips it, so a missing file reads as "pack not published" rather
    than "download interrupted". Writing to a temp directory and renaming also
    makes two workers racing on the same directory idempotent.

    Raises `SchemaPackFetchFailedError` if nothing at all was written — see
    `raise_for_empty`.
    """

    fetched: list[str] = []
    for pack in packs:
        staged = _download_pack(pack, pages_host=pages_host, api_host=api_host)
        if staged is None:
            continue
        _publish(staged, dest / pack / _VERSION)
        fetched.append(pack)

    raise_for_empty(fetched=tuple(fetched))
    return tuple(fetched)


def _download_pack(
    pack: str, *, pages_host: str, api_host: str
) -> dict[str, bytes] | None:
    files: dict[str, bytes] = {}
    for filename in _ESSENTIAL_FILES:
        body = _get(pack_file_url(pack=pack, filename=filename, host=pages_host))
        if body is None:
            return None
        files[filename] = body

    for filename in _OPTIONAL_FILES:
        body = _get(pack_file_url(pack=pack, filename=filename, host=pages_host))
        if body is not None:
            files[filename] = body

    for name in _example_names(pack, api_host=api_host):
        body = _get(
            pack_file_url(pack=pack, filename=f"examples/{name}", host=pages_host)
        )
        if body is not None:
            files[f"examples/{name}"] = body

    return files


def _example_names(pack: str, *, api_host: str) -> tuple[str, ...]:
    """The pack's example filenames, from the Contents API.

    One call per pack, because plain HTTP cannot list a directory. An
    unreadable listing yields none rather than failing the pack: examples feed
    the capability index's categories, so losing them costs routing for that
    pack but not the boot.
    """

    body = _get(examples_api_url(pack=pack, host=api_host), headers=_api_headers())
    if body is None:
        return ()
    try:
        entries = json.loads(body)
    except json.JSONDecodeError:
        return ()
    return tuple(
        entry["name"]
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("type") == "file"
        and str(entry.get("name", "")).endswith(".json")
    )


def _api_headers() -> Mapping[str, str]:
    # Unauthenticated Contents API calls are capped at 60 an hour.
    token = os.environ.get("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get(url: str, *, headers: Mapping[str, str] | None = None) -> bytes | None:
    request = urllib.request.Request(url, headers=dict(headers or {}))  # noqa: S310 - URL is built from fixed https hosts
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310 - URL is built from fixed https hosts
            return response.read()
    except urllib.error.HTTPError:
        return None
    except OSError:
        return None


def _publish(files: dict[str, bytes], version_dir: Path) -> None:
    """Write a complete pack into place, replacing whatever was there.

    Staged inside the destination rather than the system temp directory, so
    the final move is a rename within one filesystem and cannot half-succeed.
    That means creating the destination first: the startup fetch runs
    precisely when it does not exist yet.
    """

    version_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=version_dir.parent.parent) as staging:
        staged = Path(staging) / _VERSION
        for relative, body in files.items():
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)

        version_dir.parent.mkdir(parents=True, exist_ok=True)
        if version_dir.exists():
            shutil.rmtree(version_dir)
        shutil.move(str(staged), str(version_dir))


def raise_for_empty(*, fetched: tuple[str, ...]) -> None:
    if fetched:
        return
    raise SchemaPackFetchFailedError(
        f"no published schema packs found (0 of {len(PACKS)})."
        " Check that openagrinet.github.io is reachable, and set GITHUB_TOKEN if"
        " api.github.com rate-limits the examples listing."
    )
