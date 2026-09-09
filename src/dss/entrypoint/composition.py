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
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import anyio
import httpx2

from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.adapters.llm.pydantic_ai_provider import (
    PydanticAILLMProvider,
    build_azure_model,
)
from dss.adapters.llm.stub import StubLLM
from dss.adapters.schema_packs.filesystem import FilesystemSchemaPackSource
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


def build_runner(
    settings: Settings,
    *,
    client: httpx2.AsyncClient,
    fetch: FetchPacks = fetch_packs,
) -> TurnRunner:
    """Assemble the live runner.

    `client` is the caller's: it owns the connection pool, so it is also the
    only one that can close it. Nothing here keeps a reference — the two
    network adapters hold it privately — so a client built in this function
    could never be closed at all.
    """

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

    discover, invocation, schemas, schema_context_index = _network(
        settings, client, fetch=fetch
    )

    components = Components(
        discover=discover,
        plan=build_plan(
            schemas=schemas,
            schema_context_index=schema_context_index,
            invocation=invocation,
            identity=identity,
            skills=skills,
            model=_model_for(settings, settings.planner_model),
            temperature=settings.planner_temperature,
            timeout_seconds=settings.planner_timeout_seconds,
            retries=settings.planner_retries,
        ),
        compose=build_compose(
            identity=identity,
            model=_model_for(settings, settings.composer_model),
            temperature=settings.composer_temperature,
            timeout_seconds=settings.composer_timeout_seconds,
            retries=settings.composer_retries,
        ),
    )

    return Orchestrator(
        intent_llm=_intent_llm(settings),
        moderation_llm=_moderation_llm(settings),
        policies=policies,
        components=components,
        turns=FileTurnSink(settings.turns_path),
        telemetry=FileTelemetrySink(settings.telemetry_path),
    )


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
    client: httpx2.AsyncClient,
    *,
    fetch: FetchPacks = fetch_packs,
) -> tuple[
    DiscoverProviders, CapabilityInvocation, dict[str, DomainSchema], dict[str, str]
]:
    """Wire discovery + invocation, or return the unwired stand-ins.

    Returns everything the planner needs that varies with the network: the
    per-turn `discover` callable, the `invocation` port, and the two schema
    dicts the planner validates and routes against. Unwired, the dicts are
    empty and discovery finds nobody — both harmless, because the planner is
    never reached."""

    if not settings.network_enabled:
        return _discovers_nothing, _UnwiredInvocation(), {}, {}

    _ensure_schema_packs(settings, fetch=fetch)
    source = FilesystemSchemaPackSource(root=settings.schema_pack_dir)
    cache, packs = _load_schema_packs(source)
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
    return discover, invocation, schemas, schema_context_index


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


def _load_schema_packs(
    source: FilesystemSchemaPackSource,
) -> tuple[SchemaPackCache, tuple[SchemaPackFiles, ...]]:
    """Read the packs and build the capability index.

    Blocking, because `build_runner` is part of app construction. The pack
    source is an async port — so a serving caller can offload the disk read to
    a thread — and this drives it on a worker thread of its own, since the
    thread calling the factory may already be running a loop:
    `uvicorn --factory` invokes it from inside `Server.serve()`.
    """

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(anyio.run, _refresh_schema_packs, source).result()


async def _refresh_schema_packs(
    source: FilesystemSchemaPackSource,
) -> tuple[SchemaPackCache, tuple[SchemaPackFiles, ...]]:
    cache = SchemaPackCache(source)
    await cache.refresh()
    packs = await source.fetch_packs()
    return cache, packs


def _model_for(settings: Settings, configured: str):
    """The model every agent binds, Azure-aware.

    Without Azure configured this is the model string, whose own prefix picks
    the provider — Pydantic AI's syntax, and the SDK finds the key itself.

    With Azure configured it is a built model object instead, because Azure
    needs three things a string cannot express: a per-resource endpoint, a
    deployment id in place of a model name, and the key in an `api-key`
    header.

    `DSS_AZURE_OPENAI_DEPLOYMENT` then serves every agent, since one
    deployment usually does. Leave it unset to fall back to each agent's own
    `DSS_<AGENT>_MODEL` as the deployment id, which is how two agents get
    different deployments.
    """

    if not settings.azure_enabled:
        return configured

    # The provider prefix is Pydantic AI's own syntax for picking a provider
    # from a string. Azure has already been picked by the endpoint, and the
    # rest is a deployment id — so `openai:gpt-4o-mini` falls back to
    # `gpt-4o-mini`, which on a v1 endpoint is usually the deployment name.
    _, _, deployment = configured.rpartition(":")
    return build_azure_model(
        settings.azure_openai_deployment or deployment,
        endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key,
    )


def _intent_llm(settings: Settings):
    if settings.stub_llm:
        return StubLLM()  # STUB(#83): canned answers, no network
    return PydanticAILLMProvider(
        _model_for(settings, settings.intent_model),
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _moderation_llm(settings: Settings):
    if settings.stub_llm:
        return StubLLM()
    return PydanticAILLMProvider(
        _model_for(settings, settings.moderation_model),
        temperature=settings.moderation_temperature,
        timeout=settings.moderation_timeout_seconds,
        retries=settings.moderation_retries,
    )
