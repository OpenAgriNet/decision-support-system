"""Runtime settings (spec 0002, plus the 0004 moderation LLM binding).

One source of truth per knob, each with its range declared so an unusable value
fails at startup rather than silently disabling a feature.

Each component binds its *own* model (ADR-0004): intent and moderation are
separate ``DSS_<COMPONENT>_MODEL`` knobs so a deployment can point them at
different models. The model name always comes from the environment
(``env_prefix="DSS_"``): ``DSS_INTENT_MODEL``, ``DSS_MODERATION_MODEL``, etc.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dss.config.schema_pack_fetch import DEFAULT_PACK_DIR, DEFAULT_REF

# Ships beside this module, so it resolves the same however the app is started.
DEFAULT_DISTRICT_CSV = Path(__file__).resolve().parent / "districts.csv"


class Settings(BaseSettings):
    # extra="ignore": a real deployment's .env also carries non-DSS vars the LLM
    # SDK reads directly (OPENAI_API_KEY) and the Langfuse OTEL_* keys. Without
    # this, the dotenv source treats them as unknown fields and Settings() raises.
    model_config = SettingsConfigDict(
        env_prefix="DSS_", env_file=".env", extra="ignore"
    )

    # --- intent LLM binding (spec 0002) ---
    intent_model: str = "openai:gpt-4o-mini"
    intent_temperature: float = Field(0.0, ge=0.0, le=2.0)  # a classification
    intent_timeout_seconds: float = Field(5.0, gt=0.0)
    intent_retries: int = Field(1, ge=0)

    # --- moderation LLM binding (spec 0004: separate from composition) ---
    moderation_model: str = "openai:gpt-4o-mini"
    moderation_temperature: float = Field(0.0, ge=0.0, le=2.0)  # a judgment
    moderation_timeout_seconds: float = Field(5.0, gt=0.0)
    moderation_retries: int = Field(1, ge=0)

    # --- planner LLM binding (ADR-0004: each component binds its own) ---
    # A longer timeout than the single-shot components: the planner is a loop,
    # so one run is several model round-trips plus the provider calls between
    # them.
    #
    # More retries than the default 1, because the design leans on
    # `ModelRetry` in three places — an invented field, an unknown
    # resource_id, a capability with no loaded schema — and each one costs an
    # attempt.
    planner_model: str = "openai:gpt-4o-mini"
    planner_temperature: float = Field(0.0, ge=0.0, le=2.0)  # governed codes
    planner_timeout_seconds: float = Field(30.0, gt=0.0)
    planner_retries: int = Field(3, ge=0)

    # --- composer LLM binding ---
    # Warmer than the planner by default: this one writes the farmer's answer,
    # where a little variation reads better than a fixed phrasing.
    composer_model: str = "openai:gpt-4o-mini"
    composer_temperature: float = Field(0.3, ge=0.0, le=2.0)
    composer_timeout_seconds: float = Field(30.0, gt=0.0)
    composer_retries: int = Field(1, ge=0)

    # --- provider invocation (/select) ---
    # One slow provider must not block the turn. The timeout bounds a single
    # call; attempts bound how many times a *transient* failure is retried (a
    # defect is never retried — the same malformed request fails the same
    # way). Backoff doubles per attempt, because 429 is transient too and
    # retrying at once is what caused it.
    select_timeout_seconds: float = Field(5.0, gt=0.0)
    select_attempts: int = Field(3, ge=1)  # 0 would never call the provider
    select_backoff_seconds: float = Field(0.5, ge=0.0)

    # --- provider network wiring (gated) ----------------------------------
    # Discovery and invocation are the only components that leave for the OAN
    # network, and standing them up needs endpoints a local run does not have.
    # Both are optional so the app boots without them — unset, discovery finds
    # nobody, the planner and composer never run, and a turn still gets intent
    # + moderation (both real LLM calls). Set both (`network_enabled`) to light
    # up the real end-to-end path.
    discovery_base_url: str | None = None
    invocation_base_url: str | None = None  # the provider /select endpoint
    # Defaulted, unlike the two URLs: the packs are fetched to a known place
    # (`scripts/fetch_schema_packs.py`), so every checkout has one. Anchored to
    # the module rather than the working directory — `uvicorn --factory` is
    # started from wherever the operator is. A container has no repo root, so a
    # deployment sets DSS_SCHEMA_PACK_DIR to its mounted path.
    schema_pack_dir: Path | None = DEFAULT_PACK_DIR
    # Which network-specs ref the startup fetch reads when the directory above
    # is empty. `main` carries only a README today, so a fetch on the default
    # fails loudly naming the ref that has them.
    schema_pack_ref: str = DEFAULT_REF
    # How far around the turn's location to look for a provider. Only consulted
    # once the network is wired and the turn carries a geometry.
    discovery_radius_m: int = Field(25_000, ge=0)
    # Districts and their centroids, for turning a place name the farmer said
    # ("Pune") into the point the discover call's spatial filter needs. Checked
    # in and generated by `scripts/generate_district_csv.py`, so unlike the
    # schema packs it is never fetched — a missing file is a broken build, and
    # the app refuses to boot rather than silently lose every spatial filter.
    # Anchored to the module, not the working directory, because
    # `uvicorn --factory` starts from wherever the operator is.
    district_csv_path: Path = DEFAULT_DISTRICT_CSV
    # Envelope routing ids the /select adapter stamps on each provider call.
    network_sender_id: str = "consumer.oan.dev"
    network_receiver_id: str = "oan"

    @property
    def network_enabled(self) -> bool:
        """Whether the real discovery + invocation path is configured.

        Both URLs or neither: with only one, a turn would fail deep in the
        planner rather than at startup. Better to treat a partial config as
        unwired and answer intent + moderation than to boot a runner that
        cannot plan.

        `schema_pack_dir` was a third condition until it gained a default, at
        which point it could never be unset and the check became a proxy for
        something it no longer measured. The guard moved to where it fires on
        the real condition: with the network on and no packs *loaded*, the
        composition root refuses to boot."""

        return (
            self.discovery_base_url is not None and self.invocation_base_url is not None
        )

    # Where the adopter policy pack is mounted. Unset → use the bundled defaults;
    # set-but-missing → raise (see policy_loader), never boot on a different config.
    policy_config_path: Path | None = None

    # Where the tenant's scheme catalog CSV is mounted (issue #34). Nothing
    # ships in the image, so unset → no catalog, enrichment is inert, and the
    # loader warns once at boot. Set-but-missing raises, as the policy pack does.
    schemes_config_path: Path | None = None

    # How close a farmer's spelling has to be to count as an alias ("makna"
    # for "makhana"). Tried only after every exact lookup misses. `None` turns
    # similarity matching off; the floor is above 0, which matches anything.
    scheme_fuzzy_threshold: float | None = Field(0.85, gt=0.5, le=1.0)

    # --- HTTP entrypoint (ADR-0006) ---------------------------------------
    # The concrete build that served the turn, echoed as `context.version`.
    dss_release: str = "v1.0.0"
    # A cap, not a rate limit — per-user limits belong to the caller. Zero
    # refuses every turn, which is how the saturated path is tested.
    # max_concurrent_turns: int = Field(32, ge=0) # future plan based on need
    # Checked after decompression: a small gzip payload can expand well past it.
    max_body_bytes: int = Field(1_000_000, ge=1)
    # Stands in for a real readiness probe until there is a dependency to probe.
    ready: bool = True
    # Wire StubLLM instead of a real provider, so the endpoint can be exercised
    # with no API key and no network (docs/RUNNING.md). Never true in a
    # deployment — the answers are canned.
    stub_llm: bool = False

    # --- evidence ---------------------------------------------------------
    # The external evidence API does not exist yet, so records are written here
    # as JSON Lines in the meantime.
    evidence_dir: Path = Path("var/evidence")
    # Declared so the destination is configuration rather than a code change.
    # NOT wired — `entrypoint.composition.build_runner` warns if it is set.
    evidence_url: str | None = None

    @property
    def telemetry_path(self) -> Path:
        return self.evidence_dir / "telemetry.jsonl"

    @property
    def turns_path(self) -> Path:
        return self.evidence_dir / "turns.jsonl"
