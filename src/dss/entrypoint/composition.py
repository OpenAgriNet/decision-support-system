"""The composition root — the one place that names every concrete type.

Swapping a stub for a real implementation is a change here and nowhere else.
Nothing outside this module imports a concrete adapter.

One provider per component, because each may point at a different model
(ADR-0004). Policies are loaded for the moderation checkpoint only; other
checkpoints are declared but not yet evaluated.

The live runner is `orchestration.orchestrator.Orchestrator`: intent,
moderation, discovery, the planner agent and the composer, streamed to the
transport. The planner and composer are always wired — they make real model
calls — but discovery and invocation leave for the OAN network, which a local
run does not have. Those two are gated on `settings.network_enabled`: unset,
discovery finds nobody and the planner/composer are never reached, so a turn
still gets a real intent + moderation answer without any network. Supply the
three network settings to light the whole path up.
"""

from __future__ import annotations

import logging
import os
import threading
import warnings
from collections.abc import Awaitable, Callable
from datetime import datetime

import anyio
import httpx

from dss.adapters.area_lookup.csv_lookup import CsvAreaLookup
from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.adapters.llm.pydantic_ai_provider import (
    PydanticAILLMProvider,
    build_azure_model,
)
from dss.adapters.llm.stub import StubLLM
from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
from dss.adapters.scheme_catalog.csv_file import load_scheme_catalog
from dss.adapters.sinks.file import FileTelemetrySink, FileTurnSink
from dss.config.identity_loader import load_identity
from dss.config.policy_loader import load_policy_pack
from dss.config.schema_pack_fetch import SchemaPackFetchFailed, fetch_packs
from dss.config.settings import Settings
from dss.config.skill_loader import load_skills
from dss.core.intent.models import Intent
from dss.core.planner.validation import DomainSchema, parse_domain_schema
from dss.core.policy.models import Checkpoint
from dss.core.provider_discovery.index import PACK_DEFECTS, extract_type_const
from dss.core.provider_discovery.models import DiscoveryResult, SchemaPackFiles
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.shared.models import UserTurn
from dss.orchestration.compose import build_compose
from dss.orchestration.discovery import (
    DiscoverProviders,
    build_capability_discovery,
    build_discover_providers,
)
from dss.orchestration.orchestrator import Components, Orchestrator
from dss.orchestration.plan import build_plan
from dss.ports.area_lookup import AreaLookup
from dss.ports.invocation import CapabilityInvocation
from dss.ports.turn import TurnRunner

logger = logging.getLogger(__name__)

# The startup fetch, as a seam. Injected so a test can supply one that writes
# fixture packs or asserts it was never called — nothing here should reach
# GitHub from a test run.
FetchPacks = Callable[..., tuple[str, ...]]

UNWIRED_URL = (
    "DSS_EVIDENCE_URL is set but posting evidence to an external endpoint is not "
    "implemented — records are being written to {directory} instead. Two things "
    "have to be settled first: the HTTP client dependency, and what an "
    "unreachable endpoint should do to a turn (the turn sink is required, so a "
    "failed write currently fails the turn)."
)


def build_runner(settings: Settings, *, fetch: FetchPacks = fetch_packs) -> TurnRunner:
    """The runner alone, for callers that do not own the network client's
    lifecycle (tests, and anything running the unwired path). The process entry
    point uses `build_runner_with_lifecycle` so it can close the client on
    shutdown."""

    runner, _aclose = build_runner_with_lifecycle(settings, fetch=fetch)
    return runner


def build_runner_with_lifecycle(
    settings: Settings,
    *,
    fetch: FetchPacks = fetch_packs,
) -> tuple[TurnRunner, Callable[[], Awaitable[None]]]:
    """The runner and a coroutine that releases what it holds.

    The wired network path opens one shared `httpx.AsyncClient` for the whole
    process — discovery and invocation both call through it — and `aclose`
    closes it on shutdown. Unwired, nothing is opened and `aclose` is a no-op.
    `app.create_app` drives this from the FastAPI lifespan so the client is
    closed once, when the server stops, rather than leaked."""

    if settings.evidence_url:
        warnings.warn(
            UNWIRED_URL.format(directory=settings.evidence_dir),
            UserWarning,
            stacklevel=2,
        )

    policies = load_policy_pack(settings.policy_config_path).for_checkpoint(
        Checkpoint.MODERATION
    )
    identity = load_identity()  # bundled default until an adopter mounts one
    skills = load_skills()
    # Loaded before the network gate, and unconditionally: the file is checked
    # in, so an unreadable one is a broken build either way, and a boot that
    # skipped it would only surface the problem as missing spatial filters much
    # later. Read once here — every turn shares this index.
    area_lookup = CsvAreaLookup.load(settings.district_csv_path)
    # The scheme catalog is the other way round: nothing ships, because which
    # schemes a deployment serves is the tenant's call. Unset is inert and the
    # loader says so.
    scheme_catalog = load_scheme_catalog(settings.schemes_config_path)

    discover, invocation, schemas, schema_context_index, client = _network(
        settings, area_lookup=area_lookup, fetch=fetch
    )

    components = Components(
        discover=discover,
        plan=build_plan(
            schemas=schemas,
            schema_context_index=schema_context_index,
            invocation=invocation,
            identity=identity,
            skills=skills,
            model=_resolve_model(settings.planner_model),
            temperature=settings.planner_temperature,
            timeout_seconds=settings.planner_timeout_seconds,
            retries=settings.planner_retries,
        ),
        compose=build_compose(
            identity=identity,
            model=_resolve_model(settings.composer_model),
            temperature=settings.composer_temperature,
            timeout_seconds=settings.composer_timeout_seconds,
            retries=settings.composer_retries,
        ),
    )

    runner = Orchestrator(
        intent_llm=_intent_llm(settings),
        moderation_llm=_moderation_llm(settings),
        policies=policies,
        scheme_catalog=scheme_catalog,
        scheme_fuzzy_threshold=settings.scheme_fuzzy_threshold,
        components=components,
        turns=FileTurnSink(settings.turns_path),
        telemetry=FileTelemetrySink(settings.telemetry_path),
        area_lookup=area_lookup,
        discovery_radius_m=settings.discovery_radius_m,
    )
    return runner, _aclose_for(client)


# --- discovery + invocation (gated) --------------------------------------


class _UnwiredInvocation:
    """Stands in for `CapabilityInvocation` when the network is not configured.

    It is never called: discovery finds nobody, so the orchestrator answers
    NO_MATCH before the planner runs and before any `select` could fire. It
    raises rather than returns so a wiring mistake that *did* reach a provider
    call surfaces loudly instead of silently answering from nothing."""

    async def select(self, capability, resource_attributes, transaction_id):  # noqa: ANN001
        raise RuntimeError(
            "provider invocation is not configured — set DSS_INVOCATION_BASE_URL, "
            "DSS_DISCOVERY_BASE_URL and DSS_SCHEMA_PACK_DIR to call providers"
        )


async def _discovers_nothing(
    intent: Intent, turn: UserTurn, *, now: datetime
) -> DiscoveryResult:
    """Every turn finds nobody to call. The seam is typed and in place, so
    supplying the three network settings replaces this with the real client and
    lights up the planner and composer."""

    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


def _network(
    settings: Settings,
    *,
    area_lookup: AreaLookup,
    fetch: FetchPacks = fetch_packs,
) -> tuple[
    DiscoverProviders,
    CapabilityInvocation,
    dict[str, DomainSchema],
    dict[str, str],
    httpx.AsyncClient | None,
]:
    """Wire discovery + invocation, or return the unwired stand-ins.

    Returns everything the planner needs that varies with the network — the
    per-turn `discover` callable, the `invocation` port, and the two schema
    dicts the planner validates and routes against — plus the shared HTTP
    client whose lifecycle the caller owns (`None` when unwired). Unwired, the
    dicts are empty and discovery finds nobody — all harmless, because the
    planner is never reached."""

    if not settings.network_enabled:
        return _discovers_nothing, _UnwiredInvocation(), {}, {}, None

    _ensure_schema_packs(settings, fetch=fetch)

    # The client is constructed here, not in the loop below: it is used later
    # from uvicorn's loop for real requests, so binding it to the throwaway
    # startup loop would break the first one.
    client = httpx.AsyncClient(timeout=settings.select_timeout_seconds)
    source = FilesystemSchemaPackSource(root=settings.schema_pack_dir)
    cache, packs = _load_schema_packs_blocking(source)
    if not packs:
        raise ValueError(
            f"the network is configured but no schema packs loaded from "
            f"{settings.schema_pack_dir} — refusing to boot, because every turn "
            f"would come back no_match and look like 'no provider serves this'. "
            f"Run: uv run python scripts/fetch_schema_packs.py "
            f"--ref schema-packs-v0.1"
        )

    discovery = build_capability_discovery(
        client=client,
        base_url=settings.discovery_base_url,
        schema_pack_cache=cache,
    )
    discover = build_discover_providers(
        discovery=discovery,
        schema_pack_cache=cache,
        area_lookup=area_lookup,
        radius_m=settings.discovery_radius_m,
    )
    invocation = HttpCapabilityInvocation(
        client=client,
        base_url=settings.invocation_base_url,
        sender_id=settings.network_sender_id,
        receiver_id=settings.network_receiver_id,
        attempts=settings.select_attempts,
        backoff_seconds=settings.select_backoff_seconds,
    )

    for skip in cache.skipped_packs():
        logger.warning(
            "schema pack %r left out of the capability index: %s",
            skip.pack_name,
            skip.reason,
        )
    skipped = {skip.pack_name for skip in cache.skipped_packs()}
    good = tuple(pack for pack in packs if pack.pack_name not in skipped)
    schemas = _planner_schemas(good)
    schema_context_index = cache.current_schema_context()
    return discover, invocation, schemas, schema_context_index, client


async def _aclose_nothing() -> None:
    """The unwired path opened no client, so there is nothing to release."""

    return None


def _aclose_for(
    client: httpx.AsyncClient | None,
) -> Callable[[], Awaitable[None]]:
    """A single coroutine that closes the shared client, or a no-op when there
    is none. Keeping the shape identical either way means the lifespan does not
    branch on whether the network was wired."""

    if client is None:
        return _aclose_nothing

    async def aclose() -> None:
        await client.aclose()

    return aclose


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
    if directory is None or any(directory.glob("*/*/attributes.yaml")):
        return

    try:
        fetch(ref=settings.schema_pack_ref, dest=directory)
    except SchemaPackFetchFailed as failure:
        logger.warning("schema pack fetch found nothing: %s", failure)


def _planner_schemas(packs: tuple[SchemaPackFiles, ...]) -> dict[str, DomainSchema]:
    """The filter surface the planner validates a `select` against, keyed by the
    advertised @type — not the pack folder name, which differs: the planner
    looks a capability up by `capability.capability` (the @type).

    A pack with no `filterable_paths`, or one whose `@type` cannot be read at
    all, is left out rather than fatal: `provider_discovery.index` already
    treats a malformed pack this way (`PACK_DEFECTS`), because network-specs
    is an external checkout and one bad pack must not blind every other
    capability. `extract_type_const` is the same reader that builds the
    capability index, so the two never disagree on what a pack's @type is."""

    schemas: dict[str, DomainSchema] = {}
    for pack in packs:
        try:
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


async def _load_schema_packs(
    source: FilesystemSchemaPackSource,
) -> tuple[SchemaPackCache, tuple[SchemaPackFiles, ...]]:
    cache = SchemaPackCache(source)
    await cache.refresh()
    packs = await source.fetch_packs()
    return cache, packs


def _load_schema_packs_blocking(
    source: FilesystemSchemaPackSource,
) -> tuple[SchemaPackCache, tuple[SchemaPackFiles, ...]]:
    """Load the packs once, synchronously, from `build_runner`'s sync context.

    The read is async (`SchemaPackCache.refresh`), but `uvicorn --factory` calls
    the app factory from *inside* its event loop, so `anyio.run` here would
    raise "Already running asyncio in this thread". Running it on a fresh thread
    gives the read its own loop without touching the caller's. It is one-time
    startup I/O returning plain data — no loop-bound object escapes the thread,
    and the shared client is created on the caller's thread, not this one."""

    box: dict[str, tuple[SchemaPackCache, tuple[SchemaPackFiles, ...]]] = {}
    error: dict[str, BaseException] = {}

    def _run() -> None:
        try:
            box["result"] = anyio.run(_load_schema_packs, source)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            error["error"] = exc

    thread = threading.Thread(target=_run, name="dss-schema-pack-load")
    thread.start()
    thread.join()
    if error:
        raise error["error"]
    return box["result"]


def _resolve_model(model: str):
    """Turn a component's model string into what Pydantic AI should bind.

    An ``azure:<deployment>`` string is built here into a concrete Responses-API
    ``Model`` (endpoint + key from the environment, the same ``AZURE_OPENAI_*``
    vars the SDK reads directly), because pydantic-ai's own ``azure:`` inference
    routes to the classic ``?api-version=`` provider, which the v1 GA endpoint
    rejects. The deployment id is the part after ``azure:`` — so per-component
    bindings (ADR-0004) stay independent. Any other string (``openai:...``) is
    handed back untouched for pydantic-ai to infer.
    """

    if not model.startswith("azure:"):
        return model
    deployment = model.split(":", 1)[1]
    try:
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        api_key = os.environ["AZURE_OPENAI_API_KEY"]
    except KeyError as exc:
        raise RuntimeError(
            f"{model!r} needs AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in "
            "the environment (they are read by the SDK, not from DSS settings)."
        ) from exc
    return build_azure_model(deployment, endpoint=endpoint, api_key=api_key)


def _intent_llm(settings: Settings):
    if settings.stub_llm:
        return StubLLM()  # STUB(#83): canned answers, no network
    return PydanticAILLMProvider(
        _resolve_model(settings.intent_model),
        name="intent-classifier",
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _moderation_llm(settings: Settings):
    if settings.stub_llm:
        return StubLLM()
    return PydanticAILLMProvider(
        _resolve_model(settings.moderation_model),
        name="moderator",
        temperature=settings.moderation_temperature,
        timeout=settings.moderation_timeout_seconds,
        retries=settings.moderation_retries,
    )
