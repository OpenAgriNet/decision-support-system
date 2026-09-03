# TODO

## Provider Discovery (#79) — deferred to separate PRs

- **Sink routing.** `TurnSink` and `TelemetrySink` ports don't exist yet —
  only named in `docs/.agent/plan/79-provider-discovery.md`. `discover_providers`
  already returns typed events (`DiscoveryEvent`); routing them to the two
  sinks per the plan's table, with redaction at that single write point, is
  its own design piece.
- **network-specs checkout.** Nothing clones or pulls the pinned commit onto
  disk — `FilesystemSchemaPackSource` assumes a checkout already exists at a
  configured path. Also need a way to trigger a re-checkout together with
  `SchemaPackCache.refresh()` (likely a cron-driven endpoint). Tracked as
  assumption #8 in the plan doc.
