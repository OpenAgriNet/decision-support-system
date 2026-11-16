"""Reading the schema packs at boot. Discovery, settlement, the planner and
the forms all start from them, and each needs them read once, the same way."""

from __future__ import annotations

import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio

from dss.adapters.schema_packs.filesystem import (
    FilesystemSchemaPackSource,
    has_packs,
)
from dss.config.schema_pack_fetch import (
    PUBLISHED_REF,
    SchemaPackFetchFailedError,
    fetch_packs,
)
from dss.config.settings import Settings
from dss.core.forms.registry import FormRegistry
from dss.core.forms.service import build_form_registry
from dss.core.planner.validation import DomainSchema, parse_domain_schema
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.schema_packs.models import SchemaPackFiles
from dss.core.schema_packs.service import (
    PACK_DEFECTS,
    extract_type_const,
    is_api_pack,
)

logger = logging.getLogger(__name__)

# The startup fetch, as a seam. Injected so a test can supply one that writes
# fixture packs or asserts it was never called — nothing here should reach
# GitHub from a test run.
FetchPacks = Callable[..., tuple[str, ...]]


@dataclass(frozen=True)
class SchemaPacks:
    """The capability index, and the good packs it was built from."""

    cache: SchemaPackCache
    packs: tuple[SchemaPackFiles, ...]


def load_schema_packs(
    settings: Settings, *, fetch: FetchPacks = fetch_packs
) -> SchemaPacks | None:
    """None when the network is off. Then no packs are read."""

    if not settings.network_enabled:
        return None
    if settings.schema_pack_dir is None:
        raise ValueError(
            "the network is configured but DSS_SCHEMA_PACK_DIR is empty. "
            "Discovery needs the schema packs. Set it, or leave it unset for "
            "the default."
        )
    _ensure_schema_packs(settings, fetch=fetch)
    source = FilesystemSchemaPackSource(root=settings.schema_pack_dir)
    cache, packs = _run_on_fresh_loop(_read_packs, source)
    if not packs:
        raise ValueError(
            f"the network is configured but no schema packs loaded from "
            f"{settings.schema_pack_dir} — refusing to boot, because every turn "
            f"would come back no_match and look like 'no provider serves this'. "
            f"Run: uv run python scripts/fetch_schema_packs.py "
            f"--ref {PUBLISHED_REF}"
        )
    for skip in cache.skipped_packs():
        logger.warning(
            "schema pack %r left out of the capability index: %s",
            skip.pack_name,
            skip.reason,
        )
    skipped = {skip.pack_name for skip in cache.skipped_packs()}
    good = tuple(pack for pack in packs if pack.pack_name not in skipped)
    return SchemaPacks(cache=cache, packs=good)


def _ensure_schema_packs(settings: Settings, *, fetch: FetchPacks) -> None:
    """Fetch the packs once if none are on disk.

    Deliberately no on/off flag. Packs present means no network call at all;
    packs absent means the run cannot work, so there is no third behaviour to
    configure. A deployment that mounts them never reaches the fetch.

    The emptiness test mirrors `FilesystemSchemaPackSource._is_pack` rather
    than asking whether the directory has any entries: a stray `.DS_Store`
    would read as non-empty, the fetch would be skipped, and the refusal below
    would then tell you to run the fetch that was skipped.

    A failed fetch is not raised here. The refusal below covers it, and says
    which directory is empty as well as what to run — more use than a bare
    HTTP error.
    """

    directory = settings.schema_pack_dir
    if directory is None or has_packs(directory):
        return

    try:
        fetch(ref=settings.schema_pack_ref, dest=directory)
    except SchemaPackFetchFailedError as failure:
        logger.warning("schema pack fetch found nothing: %s", failure)


def planner_schemas(packs: tuple[SchemaPackFiles, ...]) -> dict[str, DomainSchema]:
    """The filter surface the planner validates a `select` against, keyed by the
    advertised @type — not the pack folder name, which differs: the planner
    looks a capability up by `capability.capability` (the @type).

    A pack with no `filterable_paths`, or one whose `@type` cannot be read at
    all, is left out rather than fatal: `provider_discovery.index` already
    treats a malformed pack this way (`PACK_DEFECTS`), because network-specs
    is an external checkout and one bad pack must not blind every other
    capability. `extract_type_const` is the same reader that builds the
    capability index, so the two never disagree on what a pack's @type is.

    An API pack is left out: its calls become forms, not filters to check."""

    schemas: dict[str, DomainSchema] = {}
    for pack in packs:
        try:
            if is_api_pack(pack):
                continue
            schema = parse_domain_schema(pack)
            type_const = extract_type_const(pack.attributes_yaml, pack.pack_name)
        except PACK_DEFECTS as exc:
            logger.warning(
                "skipping malformed schema pack %r for the planner: %r",
                pack.pack_name,
                exc,
            )
            continue
        schemas[type_const] = schema
    return schemas


async def _read_packs(
    source: FilesystemSchemaPackSource,
) -> tuple[SchemaPackCache, tuple[SchemaPackFiles, ...]]:
    cache = SchemaPackCache(source)
    await cache.refresh()
    packs = await source.fetch_packs()
    return cache, packs


def _run_on_fresh_loop[T, *Ts](read: Callable[[*Ts], Awaitable[T]], *args: *Ts) -> T:
    """Run a one-time async startup read from `build_runner`'s sync context.

    `uvicorn --factory` calls the app factory from *inside* its event loop, so
    `anyio.run` here would raise "Already running asyncio in this thread".
    Running it on a fresh thread gives the read its own loop without touching
    the caller's. Only plain data may come back: nothing bound to that loop
    can outlive the thread."""

    box: dict[str, T] = {}
    error: dict[str, BaseException] = {}

    def _run() -> None:
        try:
            box["result"] = anyio.run(read, *args)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            error["error"] = exc

    thread = threading.Thread(target=_run, name="dss-boot-read")
    thread.start()
    thread.join()
    if error:
        raise error["error"]
    return box["result"]


def boot_forms(settings: Settings) -> FormRegistry:
    """The forms the farmer can be offered, built once from the API packs.
    No packs is an empty registry and a log line, never a failed boot."""

    directory = settings.schema_pack_dir
    if directory is None:
        logger.info("DSS_SCHEMA_PACK_DIR is not set, so forms are off")
        return FormRegistry()
    if not has_packs(directory):
        logger.warning(
            "no schema packs in %s, so no forms will be offered. Run: "
            "uv run python scripts/fetch_schema_packs.py --ref %s",
            directory,
            PUBLISHED_REF,
        )
        return FormRegistry()

    source = FilesystemSchemaPackSource(root=directory)
    registry = build_form_registry(_run_on_fresh_loop(source.fetch_packs))
    for skip in source.skipped_packs():
        logger.warning("pack %r unreadable: %s", skip.pack_name, skip.reason)
    for skip in registry.skipped:
        logger.warning("form left out of pack %r: %s", skip.pack_name, skip.reason)
    if not registry:
        # A folder from an older fetch has catalog packs but no API pack.
        logger.warning(
            "no API schema pack in %s, so no forms will be offered. Delete the "
            "folder and run: uv run python scripts/fetch_schema_packs.py --ref %s",
            directory,
            PUBLISHED_REF,
        )
    return registry
