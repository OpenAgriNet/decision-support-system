# Changelog

## [Unreleased]

### Added
- Scheme catalog: a `SchemeCatalog` port over a tenant-mounted CSV
  (`scheme_code,scheme_name,scheme_aliases`), indexed by normalized alias at
  boot. Nothing ships in the image; unset is inert plus a warning (#34)
- `core/enrichment`: resolves a scheme ask's `agriculture_subjects` to the
  official scheme name by longest whole-token alias span, gated on the
  classifier having already said `Scheme`. Not wired yet (#34)
- ADR-0007: scheme names resolved deterministically at intent time, as a
  pre-discovery hint rather than a governed-code source (#34)
- Scheme enrichment wired into the turn, between intent and discovery, with a
  `trace_component("enrichment")` span and a `scheme_resolved` trace line per
  match; `Orchestrator` now requires a catalog (#35)
- `log_event` trace helper for a decision a component made, as distinct from
  the enter/exit span saying it ran (#35)
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
- `Settings()` no longer raises when `.env` carries non-DSS vars the LLM SDK and
  Langfuse read directly (`OPENAI_API_KEY`, `OTEL_*`) — `extra="ignore"` (#57)
- `/discover` now matches its spatial filter against
  `resources[*].resourceAttributes.coverageAreas[*]` — where a resource
  *applies* — instead of `provider.availableAt[*].geo`, the provider's own
  location, which wrongly excluded a provider serving a district it is not
  based in. The JSONPath filter matches `subjectCategories` rather than
  `@type`, since the envelope's `schemaContext` already names the resolved
  type (#19)
