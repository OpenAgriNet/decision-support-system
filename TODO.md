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
  and the planner/composer are never reached. **`CoreRunner` and the stub
  `core/channel` `compose` have now been removed** — the orchestrator is the
  only runner.
- **A tier 7 smoke test.** `UserTurn` → text, with the network and LLM
  stubbed. `tests/unit/entrypoint/test_app.py` covers the no-network
  (`no_match`) path today; a provider-backed variant needs a recorded
  discovery/select fixture end to end.
- **DONE — the select client is opened and closed with the process.** The
  wired branch of `composition._network` constructs the shared
  `httpx.AsyncClient` with `DSS_SELECT_TIMEOUT_SECONDS`, and
  `build_runner_with_lifecycle` hands `create_app` an `aclose` the FastAPI
  lifespan calls on shutdown, so the connection pool is released rather than
  leaked. The one-time schema-pack read runs on a worker thread because
  `uvicorn --factory` calls the app factory from inside its event loop, where
  `anyio.run` would raise "Already running asyncio in this thread".
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

- **network-specs checkout.** Nothing clones or pulls the pinned commit onto
  disk — `FilesystemSchemaPackSource` assumes a checkout already exists at a
  configured path. Also need a way to trigger a re-checkout together with
  `SchemaPackCache.refresh()` (likely a cron-driven endpoint). Tracked as
  assumption #8 in the plan doc.

## Stubs pending real implementations

- **STUB(#83) — `adapters/llm/stub_llm.py`.** Delete this module once a real
  provider is wired for every component. `adapters/llm/pydantic_ai_provider.py`
  already implements the port; the stub is selected only when
  `Settings.stub_llm` is set, and the whole file goes with that flag. It holds
  no business rules — it decides nothing about confidence, and a missing
  schema is a wiring mistake rather than a runtime path; it exists only to
  record what it was asked so a test can assert _that_ a stage ran, or that a
  stage was skipped.

- **DONE — STUB(#84) removed from `core/channel/service.py`.** The stub
  `compose()` and its hardcoded `_SOURCE`/wheat-price blocks are gone; the real
  composer is `orchestration/compose.py` (`Evidence` → prose), and
  `core/channel/service.py` now only does the deterministic shaping
  (`answer_from_evidence`, `no_match_answer`). Still open: per-ask claims and
  sub-sentence citation spans — `answer_from_evidence` emits one block today.
