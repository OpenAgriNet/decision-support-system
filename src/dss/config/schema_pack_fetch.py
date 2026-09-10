"""Fetches network-specs schema packs onto the filesystem.

The packs are an external checkout: they live in the `OpenAgriNet/network-specs`
repo, not this one, and nothing clones them. This module pulls the ones the DSS
routes against onto disk, where `FilesystemSchemaPackSource` reads them.

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
from pathlib import Path

_REPO = "OpenAgriNet/network-specs"
_RAW_HOST = "https://raw.githubusercontent.com"
_API_HOST = "https://api.github.com"

# The only version published today. When v0.2 lands this becomes a per-pack
# lookup, not a constant.
_VERSION = "v0.1"

# The ref that actually carries packs today. `main` holds only a README, so a
# failure quotes this to say where to look — see `raise_for_empty`.
_PUBLISHED_REF = "schema-packs-v0.1"

# `main` is where the packs are expected to land, so that is what we track —
# even though it carries only a README today and a default run therefore
# fails. Failing loudly with the right ref named beats silently reading a
# pinned branch nobody chose.
DEFAULT_REF = "main"

# Where the packs go. Anchored to this file, not the working directory: the
# service is started with `uvicorn --factory` from wherever the operator
# happens to be, and a relative default would resolve against that.
#
# `parents[3]` is the repo root from `src/dss/config/`. That holds for a source
# checkout, which is what the default is for. An installed wheel puts this
# module under `site-packages`, where there is no repo root and no
# `var/` — a deployment sets DSS_SCHEMA_PACK_DIR to a mounted path instead.
DEFAULT_PACK_DIR = Path(__file__).parents[3] / "var" / "schema-packs"

# The packs the DSS routes against.
#
# AgricultureResource is not routed to directly — the other three `$ref` it
# with a relative path (`../../AgricultureResource/v0.1/attributes.yaml`), so
# it has to be on disk beside them. Nothing resolves those refs today
# (`yaml.safe_load` treats `$ref` as a plain string key), so it looks unused;
# it is not. Do not drop it.
#
# AgricultureFacility is left out on purpose: none of its examples declares
# `subjectCategories`, which `provider_discovery.index` reads unguarded, so the
# pack is skipped as defective and never reaches the capability index. See
# TODO.md.
PACKS = ("AgricultureResource", "MandiPrice", "WeatherObservation", "KnowledgeAdvisory")

# What the loader needs. Absent either, the pack is not on this ref.
_ESSENTIAL_FILES = ("attributes.yaml", "profile.json")

# Fetched so the tree mirrors upstream, though nothing reads them today. A 404
# on one is not a reason to drop the pack.
_OPTIONAL_FILES = ("context.jsonld", "vocab.jsonld", "renderer.json", "README.md")

_TIMEOUT_SECONDS = 30


class SchemaPackFetchFailed(RuntimeError):
    """The fetch produced nothing usable. Never raised for a partial result."""


def raw_url(*, ref: str, pack: str, filename: str, host: str = _RAW_HOST) -> str:
    """Where one file of one pack lives, on one ref.

    The ref sits in the path, so it appears in any URL a failure reports —
    which matters because the default ref carries no packs at all.

    `host` is overridable so a test can serve the same layout over a real
    socket; production never passes it.
    """

    return f"{host}/{_REPO}/{ref}/schema/{pack}/{_VERSION}/{filename}"


def examples_api_url(*, ref: str, pack: str, host: str = _API_HOST) -> str:
    """Where to list a pack's `examples/` directory.

    The example filenames vary per pack and plain HTTP cannot list a
    directory, so this is the one API call per pack. Note the ref is a query
    parameter here, not part of the path: leave it off and the API lists the
    default branch instead of erroring.
    """

    return f"{host}/repos/{_REPO}/contents/schema/{pack}/{_VERSION}/examples?ref={ref}"


def fetch_packs(
    *,
    ref: str,
    dest: Path,
    packs: tuple[str, ...] = PACKS,
    raw_host: str = _RAW_HOST,
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

    Raises `SchemaPackFetchFailed` if nothing at all was written — see
    `raise_for_empty`.
    """

    fetched: list[str] = []
    for pack in packs:
        staged = _download_pack(pack, ref=ref, raw_host=raw_host, api_host=api_host)
        if staged is None:
            continue
        _publish(staged, dest / pack / _VERSION)
        fetched.append(pack)

    raise_for_empty(fetched=tuple(fetched), ref=ref)
    return tuple(fetched)


def _download_pack(
    pack: str, *, ref: str, raw_host: str, api_host: str
) -> dict[str, bytes] | None:
    """Every file of one pack, keyed by its path within the version directory.

    `None` when the pack is not on this ref at all — the two essential files
    are what decides that. The other four are optional: unread today, but
    fetched so the on-disk tree mirrors upstream.
    """

    files: dict[str, bytes] = {}
    for filename in _ESSENTIAL_FILES:
        body = _get(raw_url(ref=ref, pack=pack, filename=filename, host=raw_host))
        if body is None:
            return None
        files[filename] = body

    for filename in _OPTIONAL_FILES:
        body = _get(raw_url(ref=ref, pack=pack, filename=filename, host=raw_host))
        if body is not None:
            files[filename] = body

    for name in _example_names(pack, ref=ref, api_host=api_host):
        body = _get(
            raw_url(ref=ref, pack=pack, filename=f"examples/{name}", host=raw_host)
        )
        if body is not None:
            files[f"examples/{name}"] = body

    return files


def _example_names(pack: str, *, ref: str, api_host: str) -> tuple[str, ...]:
    """The pack's example filenames, from the Contents API.

    One call per pack, because plain HTTP cannot list a directory. An
    unreadable listing yields none rather than failing the pack: examples feed
    the capability index's categories, so losing them costs routing for that
    pack but not the boot.
    """

    body = _get(examples_api_url(ref=ref, pack=pack, host=api_host))
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


def _get(url: str) -> bytes | None:
    """The body at `url`, or None if it is not there.

    A 404 is the ordinary answer for "this pack is not on this ref", so it is
    data rather than an error. `GITHUB_TOKEN` is used when set: unauthenticated
    Contents API calls are capped at 60 an hour.
    """

    request = urllib.request.Request(url)
    token = os.environ.get("GITHUB_TOKEN")
    if token and "/repos/" in url:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
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


def raise_for_empty(*, fetched: tuple[str, ...], ref: str) -> None:
    """Refuse a fetch that found no pack at all.

    Zero packs means the DSS cannot route to anything, so reporting success
    would move the failure to boot time and describe it as missing packs
    rather than an empty ref. The ref is the usual cause — the default one has
    only a README on it — so the message names it and says where to look.
    """

    if fetched:
        return

    hint = (
        ""
        if ref == _PUBLISHED_REF
        else (
            f" The published packs are on {_PUBLISHED_REF!r} — "
            f"re-run with --ref {_PUBLISHED_REF}."
        )
    )
    raise SchemaPackFetchFailed(
        f"no schema packs found on ref {ref!r} (0 of {len(PACKS)}).{hint}"
    )
