# TODO

## Planner Agent (#10) — deferred to separate PRs

- **Wire `plan()` and `compose()` into `run_turn`.** The orchestrator runs
  intent, moderation and discovery today and returns `TurnResult`. Both
  remaining components exist and are tested, but nothing calls them in
  sequence, so no turn produces an answer end to end yet. This is also where
  `Verdict` gets set from moderation's decision — the barrier is proven in
  tests but not yet exercised in a real turn. `TurnResult` becomes the
  composed response when this lands.
- **A tier 7 smoke test.** `UserTurn` → text, with the network and LLM
  stubbed. Blocked on the wiring above.
- **The select timeout setting is inert.** `DSS_SELECT_TIMEOUT_SECONDS`
  exists and defaults to 5s, but nothing in `src/` constructs the
  `httpx2.AsyncClient` — callers pass one in. It gets applied at the
  composition root, which does not exist yet.
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
