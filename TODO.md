# TODO

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

- **STUB(#84) — `core/channel/compose.py`.** Delete `_SOURCE` and the fixed
  answer sentences (`compose()`'s hardcoded wheat-price blocks) together with
  the real composer. Sources belong to the evidence a plan gathered, not to
  this module — a hardcoded provider here would silently outlive the stub and
  start citing a source no turn actually consulted. The real composer writes
  one claim per ask (`intent.asks`) from the evidence a plan gathered, and
  writes in `target_lang` for the channel; this ignores both.
