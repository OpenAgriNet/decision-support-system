# Changelog

## [Unreleased]

### Fixed
- `scripts/run-local.sh` now picks a container runtime that answers, rather
  than the first one on the PATH — an installed but unstarted podman no longer
  beats a working Docker or Colima, and `CONTAINER_RUNTIME` forces either. The
  `oan-edge` check uses `network inspect`, which both runtimes have; `network
  exists` is podman-only, so the Docker path always reported the network
  missing (#138)

### Added
- `scripts/run-local.sh`: one command for a local stack — Langfuse, the mock
  network and the DSS. Prerequisites are checked before anything starts and
  refused with the fix, never repaired: a missing `oan-edge` network, an
  `ENCRYPTION_KEY` that is not 64 hex characters, a too-short
  `LANGFUSE_INIT_USER_PASSWORD`. Secrets live in a gitignored `.env.local`,
  because `pydantic-settings` reads `.env` into `Settings` and never into
  `os.environ`, where the SDKs look (#138)
- `OPENAI_BASE_URL` / `OPENAI_API_KEY` passed through in `docker-compose.yml`,
  so a non-`azure:` model string can reach an OpenAI-compatible proxy. Unset
  by default, which leaves the Azure path unchanged (#138)
- Turn and HTTP metrics over OTLP: `dss.turn.duration`, `dss.turn.count`,
  `dss.turn.first_delta.duration` (time to first word),
  `dss.turn.composed.duration` (end of composition), `dss.turn.cost`,
  `dss.stage.duration` and `dss.stage.tokens`, plus request duration/count/status from
  `opentelemetry-instrumentation-fastapi`. Off unless
  `OTEL_METRICS_EXPORTER=otlp`, since the default endpoint is Langfuse, which
  discards metrics (#139)
- Duration and cost histograms carry bucket edges in seconds and USD. The SDK
  default is sized for milliseconds, which put nearly every value in one
  bucket. Logfire's exponential-histogram view is dropped, since a view beats
  a bucket hint (#139)
- HTTP requests publish metrics only, no spans. The instrumentor's spans became
  the trace root, added a span per streamed frame, traced the healthcheck, and
  carried the query string and exception messages (#139)
- A caller's `baggage` header is no longer copied onto spans, so it cannot set
  the Langfuse user, session or trace name (#139)
- A turn closed after its terminal event keeps its status. An SSE client
  hanging up after the answer no longer counts it as an error (#139)
- A model run that fails still records its tokens and cost, so a failing turn
  does not look cheaper than it was (#139)
- `DSS_MODEL_PROFILE` — one name for the whole model configuration, labelling
  every turn-level metric so two deployments can be compared (#139)
- `observability/stages.py` — the six stage names as a `Stage` enum, shared by
  span names and metric labels. ADR-0012 recorded their absence as a cost (#139)
- Network wire types: `NetworkAction`, `NetworkVersion`, `NetworkContext` (with
  a discover and a select shape) and `NetworkSchemaContext`, replacing bare
  string literals and two hand-built context dicts. `NetworkSchemaType` (in
  `core/provider_discovery/`, its only user) and `NetworkTransactionID` (in
  `core/shared/`) name the two vocabulary aliases in `core/` (#139)
- Scheme catalog: a `SchemeCatalog` port over a tenant-mounted CSV
  (`scheme_code,scheme_name,scheme_aliases`), indexed by normalized alias at
  boot. Nothing ships in the image; unset is inert plus a warning (#34)
- `core/enrichment`: resolves a scheme ask's `agriculture_subjects` to the
  official scheme name by longest whole-token alias span, gated on the
  classifier having already said `Scheme`. Not wired yet (#34)
- ADR-0008: scheme names resolved deterministically at intent time, as a
  pre-discovery hint rather than a governed-code source (#34)
- Scheme enrichment wired into the turn, between intent and discovery, with a
  `trace_component("enrichment")` span and a `scheme_resolved` trace line per
  match; `Orchestrator` now requires a catalog (#35)
- `log_event` trace helper for a decision a component made, as distinct from
  the enter/exit span saying it ran (#35)
- A scheme ask is now discovered on its subject category alone when the
  capability index resolves no `@type` — no published schema pack declares
  `Scheme`, so `/discover` was never called for one. The request carries the
  jsonpath filter and omits `schemaContext`; ADR-0009 (#52)
- `core/stream_response`: the composer's answer yielded in the pieces the model
  writes it in. Pieces pass through untouched, so concatenating them gives the
  completed claim exactly. ADR-0011 (#72)
- A span per stage of a turn — `dss.stage.<component>` from `trace_component`,
  plus `dss.discover`, `dss.select` and `dss.select.attempt`. So "which stage
  was slow" is answerable without reading log files (#138)
- `dss.turn` now carries `status`, the four model names, `first_delta_ms` and
  `composed_ms` — when the farmer first heard anything and when the answer
  finished writing, absent rather than
  zero on turns that never reach the composer (#138)
- `dss.stage.discovery` carries `asks_total` and `asks_failed`. One provider
  down is not a failed turn, and span status has no value between OK and
  ERROR (#138)
- ADR-0012: tracing stays in the application boundary — no tracing port, no
  span code in `core/`, enforced by `test_core_isolation` (#138)
- `LLMProvider.stream_text` — prose is not a schema, so `structured()` could not
  carry the composer. No whole-answer twin, and so no retry once a piece is out:
  re-issuing would write a different answer over words already sent (#72)
- `ClaimDelta` turn event, and the `output_text_delta` wire content type. No
  annotations on either — a block mid-write has no end index to cite over (#72)


### Changed
- `POST /v1/turns` with `Accept: text/event-stream` now sends one `claim.delta`
  per piece of the answer before `claim.completed`. Additive: `claim.completed`
  still carries the whole block, so a consumer that ignores unknown event names
  is unaffected. A JSON caller's request and response are unchanged (#72)
- Response composition moved out of `orchestration/` into `core/`: it no longer
  imports Pydantic AI, because `LLMProvider.stream_text` is now the seam. The
  prompt moved with it to `core/channel/prompt.py` (#72)
- `/select` now sends the ask's own `subjectCategories` instead of echoing back
  the ones the discovered resource advertised, so both hops of an ask agree on
  what was asked — a scheme ask no longer reaches a provider labelled `Crop`,
  and the empty array an unlabelled resource used to produce is unreachable.
  ADR-0010 (#96)
- An alias hit now sets an ask's `subject_categories` to `Scheme`, overriding
  the classifier — `subject_categories` is the only thing discovery routes on,
  and the classifier does not know "PKVY" names a scheme. A non-scheme ask is
  matched on its own extracted subject only, never the shared query (#36)
- `difflib` similarity fallback for a misspelled scheme name, after every
  exact lookup misses and only against an ask's extracted subject.
  `DSS_SCHEME_FUZZY_THRESHOLD` (default 0.85, `None` to disable); a match now
  reports whether it was exact or fuzzy (#37)
- Python project scaffolding: `pyproject.toml`, package layout, ruff, pytest (#57)
- Framework-boundary enforcement — `core/` cannot import `pydantic_ai` or
  `pydantic_graph`, checked by ruff and by an AST test (#57)
- pre-commit hooks and CI workflow (#57)
- Intent contract (`Intent`, `ActionType`) and request envelope (`UserTurn`,
  `UserDetails`) with BCP-47 language validation (#57)
- Policy schema (focused): `WordCheckPolicy` / `LlmPolicy` discriminated union,
  `PolicyPack`, and a YAML loader with the shipped default pack (#57)
- Moderation service: deterministic-first evaluation with a single batched LLM
  call, `ModerationDecision` (with `sanitized_query`/`warnings`), and fail-closed
  handling behind the `LLMProvider` port + Pydantic AI adapter (#57)
- Two policies: `profanity-filter` (redact-and-warn, non-LLM) and `delete-command`
  (LLM, hard reject) (#57)
- ADR-0002: redact-and-warn modelled as a `PROCEED`-carried transform (#57)
- Dev-only harnesses under `examples/`: a FastAPI `POST /moderate` server for curl
  testing and Logfire→OpenTelemetry export to a self-hosted Langfuse (not the
  committed entrypoint; that needs an ADR) (#57)
- District index for the `/discover` spatial filter: a place the farmer names
  ("I am from Pune") is extracted by intent as `Intent.place_name` and resolved
  to a district centroid through the new `AreaLookup` port and a checked-in
  784-row CSV, so a turn that carries no coordinates still gets a spatial
  filter. A turn with no resolvable location asks the farmer which district
  they are in (`requires_input`) instead of answering from nowhere (#19)

### Fixed
- The session id now really reaches every span. The processor meant to stamp
  it was never added: logfire's tracer provider is not an SDK one, so the
  check before adding it always failed. With no `langfuse.session.id`,
  Langfuse fell back to each agent run's own `gen_ai.conversation.id` and
  showed one session per agent. The processor now goes in through
  `logfire.configure` (#138)
- Every span in a turn carries the turn's session id, not just `dss.turn`.
  Langfuse says an attribute it filters on has to be on each span, so the
  agent runs inside a turn were showing a different session (#138)
- `dss.discover` and the select spans now say what the call was. They carried a
  name and a duration only, so six of them in a turn were indistinguishable.
  `dss.discover` gains the capability it queried and how many capabilities and
  answers came back; `dss.select` gains the provider and how many attempts it
  took, and each failed attempt the status code that failed it — so a 503 and a
  429 are no longer identically shaped boxes. Shape only — the bodies hold the farmer's query and stay on the DEBUG
  log lines, per §6.1 (#138)
- Spans carry an exception's type, not its message. `SelectFailed` embeds the
  provider's response body, which echoes the farmer's query. §6.1 (#138)
- A cancelled turn leaves red spans, not green — OpenTelemetry ignores
  `BaseException`, which is what cancellation is (#138)
- A crashed turn carries `status=error` instead of no status (#138)
- `/discover` no longer raises when a capability is missing from the
  schema-context index; raising cancelled every sibling query (#138)
- A `/discover` call's request and response log lines share a `span_id` again
  (#138)
- The turn consumers close the turn generator with `aclosing`, so an abandoned
  stream no longer logs an OpenTelemetry error per held span (#138)
- Citations now carry `sourceName` and `url`. Both were declared in the
  contract — `Annotation.sourceName` even ships an example — and never
  populated, so every annotation reached a caller as a bare `sourceId`. On the
  SSE path that id resolved against nothing until `turn.completed` arrived, so
  `Claim` now carries the sources its citations resolve against (#59)
- `sources[]` now names who authored the data, from the response's own
  `resourceAttributes.source`, rather than the network participant that served
  it — a provider relaying IMD cites IMD, falling back to its own name when the
  block is absent. `sourceUri` becomes `Source.url` when it is http(s), which
  was previously always `null`. Sources key on `(provider, originator)`, so one
  provider relaying two originators is now two citable sources (#58)
- The planner prompt now marks a field by the `format` its pack declares, as it
  already did for a list. `arrivalDate` is a `string` like every free-text
  field and `format: date` is what separates them; the model wrote "this week"
  (#55)
- The turn's geometry reaches a `location` the pack *declares*, rather than one
  it lists as filterable. `AgricultureFacility` declares it and leaves it out
  of `filterable_paths` — the search origin is not a filter over advertised
  values — so "krishi kendra near me" reached the provider with no point to
  search around. MandiPrice still gets none: it names `market.location`, the
  market's own coordinates (#55)
- The planner prompt now marks a field that takes a list. The model wrote
  `"modal, minimum, maximum"` for `supportedPriceFields`, which the pack types
  `array<string>`; validation could only reject it, and the retry carried no
  more information than the first attempt, so the budget ran out and the turn
  ended `unavailable` (#55)
- A `select` narrowing an advertised *object* now merges into it instead of
  replacing it, with the advertised value winning a conflict. The model set the
  two `market` fields `profile.json` offers and the rest of the object went
  with them — including `marketName` — and it wrote the district from the
  question into `marketCode` (#55)
- Three more `select` defects, each behind the last. `_flatten` dropped the `[]`
  a pack writes for a path inside an array, so the *correct* nested shape was
  rejected (`'parameters.parameter' is not a filterable field`) — every weather
  select cost a wasted model round trip, and the model learned to send the path
  string itself as a key, which passed and reached the provider. The turn's
  geometry no longer overrides a `location` the provider advertised, since a
  pack's `location` is sometimes the resource's own identity rather than the
  query. And the model's list now selects from the advertised one rather than
  replacing it, so a narrowed item keeps the fields the model could not send
  (#55)
- `select` now sends the attributes `/discover` returned as the base of the
  request, with the model's values narrowing them, instead of rebuilding the
  object from scratch. MandiPrice requires `market.marketName` but does not
  list it as filterable, so the model could not supply it and every call was
  rejected `SCH_SCHEMA_VALIDATION_FAILED` (#55)
- `select` now carries the farmer's own subject as a free-text filter instead of
  a label copied out of the provider's catalog. `describe_capability` showed
  every advertised list under "this provider serves only these values", so
  KnowledgeAdvisory's `topics` — which the pack declares as free text the caller
  composes — read as a closed enum, and "can i grow potato" / "i want to grow in
  pune" went out as `topics: ["Crop establishment"]` rather than `["Potato in
  Pune"]`. A field advertised under a path the pack declares filterable is the
  provider describing its own content, and is no longer shown as a vocabulary;
  the `provider-invocation` skill now says a settable field with no listed
  values is free text to write from the conversation. See ADR-0007 (#96)
- `Settings()` no longer raises when `.env` carries non-DSS vars the LLM SDK and
  Langfuse read directly (`OPENAI_API_KEY`, `OTEL_*`) — `extra="ignore"` (#57)
- `/discover` now matches its spatial filter against
  `resources[*].resourceAttributes.coverageAreas[*]` — where a resource
  *applies* — instead of `provider.availableAt[*].geo`, the provider's own
  location, which wrongly excluded a provider serving a district it is not
  based in. The JSONPath filter matches `subjectCategories` rather than
  `@type`, since the envelope's `schemaContext` already names the resolved
  type (#19)
