# TODO

## Planner Agent (#10) — deferred to separate PRs

- **`Skill` + `tool_names` duplicates Pydantic AI's `AgentCapability`. Needs
  an ADR; supersedes or amends ADR-0006.**

  `Agent(capabilities=[...])` exists in pydantic-ai 2.33.0, and
  `capabilities.Capability` bundles instructions, tools and toolsets with no
  subclassing required. It maps onto our `Skill` field for field:

  | our `Skill` | `Capability` |
  |---|---|
  | `id` | `id` |
  | `description` | `description` |
  | `guidance` | `instructions` |
  | `tool_names` + our gating in `build_planner_agent` | `tools` |
  | — | `defer_loading` |

  Verified against the installed package: an unselected capability's tools
  never reach the model, which is exactly what ADR-0006 added `tool_names`
  for. The framework's own `capabilities/AGENTS.md` says "prefer a capability
  over a new `Agent` constructor kwarg when behavior contributes
  instructions... tools" — which is what we added a kwarg for.

  **`defer_loading=True` is the deferred progressive-disclosure item, in one
  flag.** The framework gives the model a `load_capability` tool and keeps
  the guidance out of the prompt until it is called; the capability's own
  tools appear only after. The plan doc lists this as future work needing a
  `load_skill` tool we build ourselves. Verified: eager gives
  `tools=['select']` with guidance in the prompt, deferred gives
  `tools=['load_capability']` with none.

  Two things to settle first:
  - `Skill` is a `core/` type with no framework import (the hexagonal rule in
    `CLAUDE.md`), so the `Skill` → `Capability` conversion has to happen in
    `orchestration/`. Config-loaded markdown skills still work — build a
    `Capability` per loaded `Skill` at wiring time.
  - `Skill.domain` has no `Capability` equivalent. Unused today.

  Note this finding *supports* ADR-0005 (the loop belongs in
  `orchestration/`, the framework's vocabulary cannot hide behind a port)
  while undermining ADR-0006.

- **DONE — `plan()` and `compose()` are wired into the live runner.**
  `orchestration/orchestrator.py:Orchestrator` is now the `TurnRunner`
  `composition.build_runner` returns: intent, moderation and discovery
  (delegated to `run_turn`), then the planner agent → `Evidence` → the
  composer, streamed as events. `Verdict` is set from moderation's decision
  before the planner runs. Discovery + invocation are gated on the three
  network settings (`Settings.network_enabled`); unset, a turn is `no_match`
  and the planner/composer are never reached. `core_runner.CoreRunner` is
  superseded and no longer wired — remove it (and its stub `core/channel`
  `compose`) once nothing references it.
- **A tier 7 smoke test.** `UserTurn` → text, with the network and LLM
  stubbed. `tests/unit/entrypoint/test_app.py` covers the no-network
  (`no_match`) path today; a provider-backed variant needs a recorded
  discovery/select fixture end to end.
- **The select timeout setting now applies at the composition root.** The
  wired branch of `composition._network` constructs the shared
  `httpx2.AsyncClient` with `DSS_SELECT_TIMEOUT_SECONDS`. Still open: the
  client is never closed on shutdown (no lifespan hook), so a durable
  deployment leaks it — wire it into the app lifespan.
- **The composer belongs in `core/`.** It lives in `orchestration/compose.py`
  because it calls Pydantic AI directly: `LLMProvider` only offers
  `structured()`, and prose is not a schema. Adding a `text()` method to that
  port would let it move, matching the design's Response Composer.
- **Query normalisation has no home.** `enriched_query` mirrors
  `original_query` — `envelope.py` never fills it. The planner's skill
  guidance tells the model to resolve a subject across the conversation
  instead ("advisory for potato" ... "I am from Pune"). Doing it in intent's
  existing LLM call is strictly better: same latency, and the normalised
  query becomes visible to discovery and the sinks rather than being
  re-derived inside the agent per capability. Designed as "intent recognition
  and enrichment" in `DSS_ARCHITECTURE.md` §3.
- **Markdown rendering is generic, not schema-derived.**
  `render_answer_as_markdown` and `render_candidates_as_markdown` use flat
  key:value bullets. Reading each pack's per-field `description` from
  `attributes.yaml` would give the model real labels instead of bare `modal:`,
  with no rendering code per capability.
- **`Source.url` is always `None`.** Discovery carries no provider URL, so
  citations are bare `[1]` with no link. Some packs carry a `source` block
  with a `sourceUri` inside `resourceAttributes`
  (`KnowledgeAdvisory`'s is `https://knowledge.example.org`). Mining that per
  pack needs its own decision.
- **The plan doc disagrees with the code on `provider_code`.** It says select
  sends `offer.provider.descriptor.code`; the real `select_request.json`
  sends only `id` and `descriptor.name`, so nothing sends the code. The field
  is kept on `ProviderCapability` for when the network wants it. That
  section of the plan needs amending.
- **`select_request.json` is not asserted against.** It is a reference
  fixture no test reads. Asserting our built request against it would have
  surfaced the `provider_code` mismatch above automatically.
- **The `<BEGIN ...>` markers are hardcoded in three places** —
  `core/planner/markdown.py`, `core/planner/prompt.py`, and
  `core/planner/planner_prompt.md`. Change one and the prompt's instruction
  no longer matches what the code emits. Shared constants would fix it.
- **Required fields have no source.** Some `filterable` fields matter far
  more than others for a useful answer (`AgricultureFacility`'s
  `facilityType`), but neither `attributes.yaml` nor `profile.json` says
  which. Mitigated by advisory guidance in the `provider-invocation` skill,
  not by a validation gate. Matches design doc Open #3, unresolved
  network-wide.
- **One test file still uses `asyncio`.** `src/` is entirely on anyio now,
  but `tests/integration/orchestration/test_turn.py` still imports asyncio
  for its fake LLMs' `sleep(0)`. Cosmetic, but the mix invites copying the
  wrong one into a new test.

## Provider Discovery (#79) — deferred to separate PRs

- **Sink routing.** `TurnSink` and `TelemetrySink` ports don't exist yet —
  only named in `docs/.agent/plan/79-provider-discovery.md`. `discover_providers`
  already returns typed events (`DiscoveryEvent`); routing them to the two
  sinks per the plan's table, with redaction at that single write point, is
  its own design piece.

- **Per-ask fan-out inflates `CategoryMappingDiverged`.** Two related things,
  to be settled together:
  1. `Ask.agriculture_subjects` is a single `str | None`, so "potato price and
     onion price" becomes two `Ask`s differing only in that field. Open:
     whether it should hold a list, collapsing such turns to one ask. Changes
     `Ask` (spec 0002) — needs an ADR.
  2. `map_on_discover_response` keys one query's result under every ask index
     that shares it (`client.py`), and the divergence scan in
     `discover_providers` walks that fan-out — so one provider's mapping
     defect emits N identical events on an N-ask turn, inflating alert counts.
     Scanning `query_results` once instead would fix it.

  Fixing (1) alone does not fix (2): two asks in different categories can
  still resolve to the same `@type` set and dedupe to one query. Whether the
  per-ask dict is the right response shape at all is the real question.

- **network-specs refresh.** `scripts/fetch_schema_packs.py` now pulls the
  packs onto disk, and the composition root fetches once when the directory is
  empty (#21). What is still missing is a way to *re-*fetch on a running
  service, together with `SchemaPackCache.refresh()` — likely a cron-driven
  endpoint. Tracked as assumption #8 in the plan doc.

- **`subjectCategories` is read unguarded.** `index.py:_extract_subject_categories`
  does `json.loads(example)["subjectCategories"]`. The field is *optional* in
  `AgricultureResource`, so a valid pack that omits it raises `KeyError`, which
  `PACK_DEFECTS` catches — the pack is silently skipped and never reaches the
  capability index. AgricultureFacility is exactly this case: none of its five
  examples declares the field, so facility queries can never route. The pack is
  valid; the extractor should use `.get(..., ())`.

- **`SubjectCategory` is narrower than the packs'.** The DSS enum has five
  values (`Crop, Livestock, Weather, Market, Scheme`); the packs' has seven,
  adding `Practice` and `Facility`. A pack advertising either can never be
  asked for, because intent cannot produce that category.

- **`_ACTION_TYPES` makes the Knowledge/Service axis inert.** `index.py` indexes
  every pack under *both* action types, so `interaction_type` narrows nothing
  and only `subject_categories` selects a capability. Explains why an OBSERVE
  ask can resolve an advisory pack.

- **Spatial filter targets a field the sample catalogs lack.** The DSS sends
  `targets: "$.catalogs[*].provider.availableAt[*].geo"`, but the real
  `on_discover` samples put coverage on
  `resources[*].resourceAttributes.coverageAreas[*]` and carry no
  `provider.availableAt`. If the network filters spatially on what the DSS
  names, such a provider would never match a located query. Found while
  building the mock network (#21).

- **`schemas.openagrinet.global` does not resolve.** DNS fails. It appears in
  test fixtures (`test_planner_select_tool.py`) and in `taxonomy.` form inside
  `subjectId` values; the real packs declare
  `openagrinet.github.io/network-specs/...`, which does resolve. Nothing
  dereferences either at runtime, so this is fixture/reality drift rather than
  a live fault.

## Stubs pending real implementations

- **STUB(#83) — `adapters/llm/stub_llm.py`.** Delete this module once a real
  provider is wired for every component. `adapters/llm/pydantic_ai_provider.py`
  already implements the port; the stub is selected only when
  `Settings.stub_llm` is set, and the whole file goes with that flag. It holds
  no business rules — it decides nothing about confidence, and a missing
  schema is a wiring mistake rather than a runtime path; it exists only to
  record what it was asked so a test can assert _that_ a stage ran, or that a
  stage was skipped.

- **STUB(#84) — `core/channel/compose.py`.** Delete `_SOURCE` and the fixed
  answer sentences (`compose()`'s hardcoded wheat-price blocks) together with
  the real composer. Sources belong to the evidence a plan gathered, not to
  this module — a hardcoded provider here would silently outlive the stub and
  start citing a source no turn actually consulted. The real composer writes
  one claim per ask (`intent.asks`) from the evidence a plan gathered, and
  writes in `target_lang` for the channel; this ignores both.
