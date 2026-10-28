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
from contextlib import AsyncExitStack
from datetime import datetime
from functools import partial
from typing import Never

import anyio
import httpx

from dss.adapters.area_lookup.cache import CachedAreaLookup
from dss.adapters.area_lookup.chain import ChainedAreaLookup
from dss.adapters.area_lookup.csv_lookup import CsvAreaLookup
from dss.adapters.area_lookup.photon import PhotonAreaLookup
from dss.adapters.invocation.client import HttpCapabilityInvocation
from dss.adapters.llm.pydantic_ai_provider import (
    ModelBinding,
    PydanticAILLMProvider,
    build_azure_model,
    build_gateway_model,
)
from dss.adapters.llm.stub import StubLLM
from dss.adapters.pii_identifier.factory import build_identifiers
from dss.adapters.provider_preferences.registry import RegistryProviderPreferences
from dss.adapters.schema_packs.filesystem import (
    FilesystemSchemaPackSource,
    has_packs,
)
from dss.adapters.scheme_catalog.csv_file import load_scheme_catalog
from dss.adapters.sinks.file import FileTelemetrySink, FileTurnSink
from dss.adapters.translator.llm import LLMTranslator
from dss.adapters.translator.stub import StubTranslator
from dss.config.clarification_text_loader import load_clarification_text
from dss.config.edge_translation_loader import (
    LLMBinding,
    StubBinding,
    TranslationBinding,
    load_edge_translation_config,
)
from dss.config.identity_loader import load_identity
from dss.config.policy_loader import load_policy_pack
from dss.config.prompt_service import build_prompt_service
from dss.config.redaction_loader import load_redaction_config
from dss.config.schema_pack_fetch import (
    PUBLISHED_REF,
    SchemaPackFetchFailedError,
    fetch_packs,
)
from dss.config.settings import Settings
from dss.core.channel.localize import build_localizer
from dss.core.forms.registry import FormRegistry
from dss.core.forms.service import build_form_registry
from dss.core.intent.models import Intent
from dss.core.planner.validation import DomainSchema, parse_domain_schema
from dss.core.policy.models import Checkpoint
from dss.core.provider_discovery.index import (
    PACK_DEFECTS,
    extract_type_const,
    is_api_pack,
)
from dss.core.provider_discovery.models import (
    DiscoveryResult,
    ProviderCapability,
    SchemaPackFiles,
)
from dss.core.provider_discovery.schema_pack_cache import SchemaPackCache
from dss.core.provider_preferences.models import NO_PREFERENCE, SenderPreference
from dss.core.redaction.visibility import NOTHING_HELD, Visibility
from dss.core.shared.models import TextQuery, TurnInput, UserTurn
from dss.core.shared.network import NetworkTransactionID
from dss.core.stream_response.service import build_stream_response
from dss.core.translation.models import TranslationRoute
from dss.core.translation.service import EdgeTranslation
from dss.observability.stages import Stage
from dss.orchestration.discovery import (
    DiscoverProviders,
    build_capability_discovery,
    build_discover_providers,
)
from dss.orchestration.orchestrator import (
    Components,
    Orchestrator,
    TextPath,
    TurnPaths,
    unrouted_kinds,
)
from dss.orchestration.plan import build_plan
from dss.orchestration.redaction import RedactTexts, pass_through, redact_texts
from dss.ports.area_lookup import AreaLookup
from dss.ports.invocation import CapabilityInvocation
from dss.ports.llm import LLMProvider
from dss.ports.prompts import PromptProvider
from dss.ports.provider_preferences import ProviderPreferences
from dss.ports.translator import Translator
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
    clarification_text = load_clarification_text()  # bundled default, same as identity
    # Every LLM component's system prompt, per language (configs/prompts.yaml).
    # Loaded once; a broken registry — a missing template, a placeholder typo,
    # no English entry — refuses to boot here rather than mid-turn.
    prompts = build_prompt_service(settings.prompt_config_path)
    # Loaded before the network gate, and unconditionally: the file is checked
    # in, so an unreadable one is a broken build either way, and a boot that
    # skipped it would only surface the problem as missing spatial filters much
    # later. Read once here — every turn shares this index.
    area_lookup, area_aclose = build_area_lookup(settings)
    # The scheme catalog is the other way round: nothing ships, because which
    # schemes a deployment serves is the tenant's call.
    scheme_catalog = load_scheme_catalog(settings.schemes_config_path)
    # Before the network, like the other boot checks: a bad rule or a missing
    # name model stops the boot here, never on a farmer's turn.
    redact = _redaction(settings)
    # Same: a bad edge translation config stops the boot here (#174).
    translation_routes = _build_translation_routes(settings, prompts=prompts)

    # Which providers each sender wants (ADR-0019). Loaded with the other boot
    # config, before the network gate: a malformed ranking must stop the boot
    # whether or not this deployment talks to providers, so an unwired local
    # run still catches the typo. Registry mode (ADR-0020) swaps the real
    # adapter in inside `_network`, where the network's HTTP client exists.
    preferences = _boot_preferences(settings)

    discover, invocation, schemas, schema_context_index, client, _preferences = (
        _network(settings, fetch=fetch, preferences=preferences)
    )
    # Forms (#205) work with the network off too. After `_network`, because
    # its startup fetch is what fills the folder they are read from.
    forms = _boot_forms(settings)

    # One binding for everything the farmer reads: the streamed answer and the
    # localized system text ride the same model, temperature and timeout.
    composer_llm = _composer_llm(settings)

    components = Components(
        redact=redact,
        discover=discover,
        plan=build_plan(
            schemas=schemas,
            schema_context_index=schema_context_index,
            invocation=invocation,
            identity=identity,
            prompts=prompts,
            model=_resolve_model(settings.planner_model, settings),
            temperature=settings.planner_temperature,
            timeout_seconds=settings.planner_timeout_seconds,
            retries=settings.planner_retries,
        ),
        compose=build_stream_response(
            identity=identity, llm=composer_llm, prompts=prompts
        ),
        # The composer's own binding (the user-facing writer, ADR-0018):
        # rendering a refusal or a clarification in the farmer's language is
        # composition work, not a new component with its own model knob.
        localize=build_localizer(llm=composer_llm, prompts=prompts),
    )

    text_path = TextPath(
        intent_llm=_intent_llm(settings),
        moderation_llm=_moderation_llm(settings),
        prompts=prompts,
        policies=policies,
        scheme_catalog=scheme_catalog,
        scheme_fuzzy_threshold=settings.scheme_fuzzy_threshold,
        nearest_max_km=settings.nearest_max_km,
        components=components,
        turns=FileTurnSink(settings.turns_path),
        telemetry=FileTelemetrySink(settings.telemetry_path),
        area_lookup=area_lookup,
        clarification_text=clarification_text,
        schemas=schemas,
        edge_translation=EdgeTranslation(routes=translation_routes),
        forms=forms,
    )
    paths: TurnPaths = {TextQuery: text_path}
    unrouted = unrouted_kinds(paths, TurnInput.__subclasses__())
    if unrouted:
        names = ", ".join(kind.__name__ for kind in unrouted)
        raise ValueError(f"no path for {names}, refusing to boot")
    runner = Orchestrator(paths=paths)
    return runner, _aclose_all(_aclose_for(client), area_aclose)


def build_area_lookup(
    settings: Settings,
) -> tuple[ChainedAreaLookup, Callable[[], Awaitable[None]]]:
    """The sources a place name is looked up in, in order, and a coroutine that
    releases what they hold. Core sees one lookup."""

    csv = CsvAreaLookup.load(settings.area_csv_path)
    sources: list[tuple[str, AreaLookup]] = [("csv", csv)]
    # Empty counts as unset: `DSS_PHOTON_BASE_URL=` is an operator turning it off.
    if not settings.photon_base_url:
        return ChainedAreaLookup(sources), _aclose_nothing

    # Its own client: Photon is a different server with a different timeout,
    # and closing it must not touch the provider network's connections.
    # httpx has its own 5 second limit, which would cut a longer setting short.
    client = httpx.AsyncClient(timeout=settings.photon_timeout_seconds)
    photon: AreaLookup = PhotonAreaLookup(
        client=client,
        base_url=settings.photon_base_url,
        country_codes=settings.photon_country_codes or csv.country_codes(),
        timeout_seconds=settings.photon_timeout_seconds,
    )
    if settings.photon_cache_enabled:
        photon = CachedAreaLookup(
            photon,
            ttl_seconds=settings.photon_cache_ttl_seconds,
            max_entries=settings.photon_cache_max_entries,
        )
    sources.append(("photon", photon))
    return ChainedAreaLookup(sources), client.aclose


def _aclose_all(
    *closers: Callable[[], Awaitable[None]],
) -> Callable[[], Awaitable[None]]:
    async def aclose() -> None:
        # The stack runs every close even if one fails, then raises that
        # failure: one stuck client must not leave the others open.
        async with AsyncExitStack() as stack:
            for close in closers:
                stack.push_async_callback(close)

    return aclose


class _NoRankings:
    """The `ProviderPreferences` of a deployment with no registry configured.

    Every ask keeps whichever providers discovery found — the behaviour that
    existed before rankings did. The DSS holds no rankings of its own
    (ADR-0020): the registry is the one source, so without it there is
    nothing to load, only this to answer with.
    """

    async def for_sender(
        self, sender_id: str | None, *, transaction_id: str
    ) -> SenderPreference:
        return NO_PREFERENCE


def _boot_preferences(settings: Settings) -> ProviderPreferences:
    """The ranking source that exists before the network does.

    Registry mode (ADR-0020) needs the network's HTTP client, so `_network`
    swaps the real adapter in once that client exists; until then — and for
    good, when no registry is configured — no ranking applies. Unset is a real
    deployment state, but an operator has to be told once, or a ranking that
    silently never applies looks exactly like a registry that returned
    nothing.
    """

    if settings.provider_preferences_base_url is None:
        logger.warning(
            "DSS_PROVIDER_PREFERENCES_BASE_URL is not set, so no sender "
            "provider ranking applies: every ask keeps whichever providers "
            "discovery found, in whatever order it found them, for every "
            "sender. Point it at the registry to have a sender's vetted "
            "providers answer first."
        )
    return _NoRankings()


def _redaction(settings: Settings) -> RedactTexts:
    """The first step of every turn (ADR-0015). Off, the texts pass through
    unchanged; on, every identifier the rules file lists is built now, so one
    that cannot load raises ``IdentifierUnavailableError`` at boot."""

    config = load_redaction_config(
        enabled=settings.redaction_enabled, path=settings.redaction_config_path
    )
    if config is None:
        return pass_through
    return partial(
        redact_texts,
        identifiers=build_identifiers(config.identifiers),
        policy=config.policy,
    )


def _build_translation_routes(
    settings: Settings, *, prompts: PromptProvider
) -> dict[str, TranslationRoute]:
    """One route per language in the edge translation config (#174); none
    when `DSS_EDGE_TRANSLATION_CONFIG_PATH` is unset."""

    return {
        language: TranslationRoute(
            translator=_build_translator(
                language, binding, settings=settings, prompts=prompts
            ),
            to=binding.to,
        )
        for language, binding in load_edge_translation_config(
            settings.edge_translation_config_path
        ).items()
    }


def _build_translator(
    language: str,
    binding: TranslationBinding,
    *,
    settings: Settings,
    prompts: PromptProvider,
) -> Translator:
    """The adapter a language's entry names. With models stubbed, every
    language gets the stub: no model is called, translation included."""

    if isinstance(binding, StubBinding) or settings.stub_llm:
        return StubTranslator(language=language)
    return LLMTranslator(
        batch_llm=_build_translator_llm(
            binding,
            name=f"translator-{language}-in",
            stage=Stage.TRANSLATION_IN,
            settings=settings,
        ),
        stream_llm=_build_translator_llm(
            binding,
            name=f"translator-{language}-out",
            stage=Stage.TRANSLATION_OUT,
            settings=settings,
        ),
        prompts=prompts,
    )


def _build_translator_llm(
    binding: LLMBinding, *, name: str, stage: Stage, settings: Settings
) -> LLMProvider:
    return PydanticAILLMProvider(
        _resolve_model(binding.model, settings),
        name=name,
        stage=stage,
        temperature=binding.temperature,
        timeout=binding.timeout_seconds,
    )


# --- discovery + invocation (gated) --------------------------------------


class _UnwiredInvocation:
    """Stands in for `CapabilityInvocation` when the network is not configured.

    It is never called: discovery finds nobody, so the orchestrator answers
    NO_MATCH before the planner runs and before any `select` could fire. It
    raises rather than returns so a wiring mistake that *did* reach a provider
    call surfaces loudly instead of silently answering from nothing."""

    async def select(
        self,
        capability: ProviderCapability,
        resource_attributes: dict,
        transaction_id: NetworkTransactionID,
        *,
        visibility: Visibility = NOTHING_HELD,
    ) -> Never:
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
    preferences: ProviderPreferences,
    fetch: FetchPacks = fetch_packs,
) -> tuple[
    DiscoverProviders,
    CapabilityInvocation,
    dict[str, DomainSchema],
    dict[str, str],
    httpx.AsyncClient | None,
    ProviderPreferences,
]:
    """Wire discovery + invocation, or return the unwired stand-ins.

    Returns everything the planner needs that varies with the network — the
    per-turn `discover` callable, the `invocation` port, and the two schema
    dicts the planner validates and routes against — plus the shared HTTP
    client whose lifecycle the caller owns (`None` when unwired), and the
    ranking source actually in force: the registry adapter rides that client
    (ADR-0020), so registry mode is resolved here. Unwired, the
    dicts are empty and discovery finds nobody — all harmless, because the
    planner is never reached."""

    if not settings.network_enabled:
        if settings.provider_preferences_base_url is not None:
            # Harmless — an unwired run calls no provider for a ranking to
            # order — but said out loud, so a half-configured deployment reads
            # as one rather than as a registry that returned nothing.
            logger.warning(
                "DSS_PROVIDER_PREFERENCES_BASE_URL is set but the network is "
                "not configured, so no provider ranking will ever apply"
            )
        return _discovers_nothing, _UnwiredInvocation(), {}, {}, None, preferences

    _ensure_schema_packs(settings, fetch=fetch)

    # The client is constructed here, not in the loop below: it is used later
    # from uvicorn's loop for real requests, so binding it to the throwaway
    # startup loop would break the first one.
    client = httpx.AsyncClient(timeout=settings.select_timeout_seconds)
    source = FilesystemSchemaPackSource(root=settings.schema_pack_dir)
    cache, packs = _run_on_fresh_loop(_load_schema_packs, source)
    if not packs:
        raise ValueError(
            f"the network is configured but no schema packs loaded from "
            f"{settings.schema_pack_dir} — refusing to boot, because every turn "
            f"would come back no_match and look like 'no provider serves this'. "
            f"Run: uv run python scripts/fetch_schema_packs.py "
            f"--ref {PUBLISHED_REF}"
        )

    discovery = build_capability_discovery(
        client=client,
        base_url=settings.discovery_base_url,
        schema_pack_cache=cache,
    )
    if settings.provider_preferences_base_url is not None:
        # The registry, asked on every turn over the same client the network
        # calls ride (ADR-0020). Swapped in here rather than at boot because
        # this client is the thing it needs.
        preferences = RegistryProviderPreferences(
            client=client,
            base_url=settings.provider_preferences_base_url,
            timeout_seconds=settings.provider_preferences_timeout_seconds,
        )
        logger.info(
            "provider rankings come from the registry at %s",
            settings.provider_preferences_base_url,
        )
    discover = build_discover_providers(
        discovery=discovery,
        schema_pack_cache=cache,
        radius_m=settings.discovery_radius_m,
        preferences=preferences,
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
    return discover, invocation, schemas, schema_context_index, client, preferences


async def _aclose_nothing() -> None:
    """The unwired path opened no client, so there is nothing to release."""

    return


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
    if directory is None or has_packs(directory):
        return

    try:
        fetch(ref=settings.schema_pack_ref, dest=directory)
    except SchemaPackFetchFailedError as failure:
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


async def _load_schema_packs(
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


def _boot_forms(settings: Settings) -> FormRegistry:
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
        # A folder fetched from `schema-packs-v0.1` has packs but no Grievance.
        logger.warning(
            "no API schema pack in %s, so no forms will be offered. Delete the "
            "folder and run: uv run python scripts/fetch_schema_packs.py --ref %s",
            directory,
            PUBLISHED_REF,
        )
    return registry


def _resolve_model(model: str, settings: Settings) -> ModelBinding:
    """Turn a component's model string into what Pydantic AI should bind.

    With ``gateway_url`` set, the string is a NAME the gateway resolves -
    ``dss-composer``, not a vendor's model id. Which vendor answers is the
    gateway's business (ADR-0013), so these four settings are meant to be set
    once and left alone: a model or vendor change happens in the gateway.

    The client is built here rather than left to Pydantic AI's inference for one
    reason - the request has to carry ``traceparent``, or the gateway files its
    spans under a trace of its own and one turn becomes seven.

    Without ``gateway_url`` the pre-gateway paths remain. An ``azure:<deployment>``
    string is built into a concrete Responses-API ``Model`` (endpoint + key from
    the environment, the same ``AZURE_OPENAI_*`` vars the SDK reads directly),
    because pydantic-ai's own ``azure:`` inference routes to the classic
    ``?api-version=`` provider, which the v1 GA endpoint rejects. Anything else is
    handed back untouched. Both go when the gateway lands everywhere.
    """

    if settings.gateway_url:
        return build_gateway_model(
            model,
            base_url=settings.gateway_url,
            api_key=settings.gateway_api_key,
        )

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


def _intent_llm(settings: Settings) -> LLMProvider:
    if settings.stub_llm:
        return StubLLM()  # STUB(#83): canned answers, no network
    return PydanticAILLMProvider(
        _resolve_model(settings.intent_model, settings),
        name="intent-classifier",
        stage=Stage.INTENT,
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _composer_llm(settings: Settings) -> LLMProvider:
    """The composer's model binding.

    Warmer than the planner's by default: this one writes the farmer's answer,
    where a little variation reads better than a fixed phrasing. `retries`
    reaches only the whole-answer call — `stream_text` does not offer one, so a
    stream that has already sent pieces cannot be restarted over the top of
    them.
    """

    if settings.stub_llm:
        return StubLLM()
    return PydanticAILLMProvider(
        _resolve_model(settings.composer_model, settings),
        name="composer",
        stage=Stage.COMPOSER,
        temperature=settings.composer_temperature,
        timeout=settings.composer_timeout_seconds,
        retries=settings.composer_retries,
    )


def _moderation_llm(settings: Settings) -> LLMProvider:
    if settings.stub_llm:
        return StubLLM()
    return PydanticAILLMProvider(
        _resolve_model(settings.moderation_model, settings),
        name="moderator",
        stage=Stage.MODERATION,
        temperature=settings.moderation_temperature,
        timeout=settings.moderation_timeout_seconds,
        retries=settings.moderation_retries,
    )
