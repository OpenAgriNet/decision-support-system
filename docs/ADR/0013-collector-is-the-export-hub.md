# ADR-0013: the collector is the export hub

- **Status:** ACCEPTED
- **Date:** 2026-09-23
- **Deciders:** DSS code owners
- **Informed:** Adopter engineering teams; OAN DPG steward

---

## 1. Context and Problem Statement

ADR-0007 sends the DSS's traces straight to a self-hosted Langfuse. That was
the right destination for the question it was asked: *why did the model say
that?* Langfuse shows the prompt, the completion and the planner's tool loop,
and it is where someone goes when a complaint arrives.

It is the wrong destination for a second question, and that question now has
data behind it. ADR-0012 timed six stages; the work after it published turn
and stage metrics — `dss.turn.duration`, `dss.stage.duration`, token counts,
cost. **Langfuse discards metrics.** So `OTEL_METRICS_EXPORTER` ships as
`none`, and everything that work produced goes nowhere.

A third signal is missing entirely. `dss.trace` writes one line per stage and
one per outbound call, each carrying `trace_id` and `span_id` as text so a
person holding the log file can grep a turn back together. Nobody looking at a
dashboard has the log file.

Meanwhile a ClickHouse and a Grafana already run on the deployment host, used
by other services.

**The question.** One OTLP stream, two destinations that want different
things. Where does the split happen, and what is allowed through each branch?

## 2. Decision Drivers

- **Metrics must land somewhere.** They exist and are exported nowhere. This
  is the driver that started it.
- **One turn, one screen.** A slow panel should lead to the trace that caused
  it and the log lines around it, without changing tools.
- **Do not widen the PII deviation.** ADR-0007 §5 put farmers' words in
  Langfuse as an explicit, bounded exception. A second store is a second copy
  and a second door, and the control that was supposed to bound it
  (`DSS_ARCHITECTURE.md` §6.2) still does not exist.
- **No new code for a routing decision.** Where telemetry goes is a
  deployment's business. It should not be a Python change.
- **Vendor neutrality**, as ADR-0007 §4 established: the seam is the protocol.

## 3. Considered Options

**A. A second exporter in the application.** The DSS sends to Langfuse and to
ClickHouse itself. Rejected on all of the last three drivers at once: the OTEL
SDK's environment variables describe one endpoint, so two would mean building
exporters in code — which is exactly what `tracing.py` avoids today — and
every routing or redaction change would then be a release.

**B. Metrics to ClickHouse, traces stay on Langfuse.** The smallest change,
and it does fix the driver that started this. Rejected on the second: a
dashboard panel and the trace explaining it would be in two systems with no
link between them, which is most of the value.

**C. Replace Langfuse with ClickHouse and Grafana.** Six containers go away.
Rejected for what it costs rather than what it saves: no prompt-and-completion
view, no sessions, no annotation or evaluation surface. ADR-0007 §3 rejected
Arize Phoenix for lacking those; deleting them outright is a larger version of
the same mistake.

**D. A collector in front, fanning out.** One OTLP stream from the DSS, split
by the collector: traces to both stores, metrics and logs to ClickHouse.

## 4. Decision Outcome

**Option D.** The collector stops being a debugging toy behind a compose
profile and becomes infrastructure.

```
DSS ──OTLP──> collector ──┬── traces (with content) ──> Langfuse
                          ├── traces (stripped)      ──> ClickHouse ──> Grafana
                          ├── metrics                ──> ClickHouse ──> Grafana
                          └── logs                   ──> ClickHouse ──> Grafana
```

**Content is deleted on the ClickHouse branch.** Six span attributes carry
message text — `gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.system_instructions`, `pydantic_ai.all_messages`,
`gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`. An `attributes`
processor deletes all six before the ClickHouse exporter sees them. Grafana's
question is "how often, how slow, how much", and none of it needs a farmer's
words.

This is why there are two `traces/*` pipelines rather than one: a pipeline
cannot branch after a processor, so a single pipeline would have to strip for
both destinations or neither.

**This is the §6.2 redaction interceptor, for one of the two sinks.**
`DSS_ARCHITECTURE.md` §6.2 describes a redaction step at the sink layer and
§8.3 records it as unbuilt. Half of it now exists, in the collector rather
than as a span processor in the process. The deviation ADR-0007 §5 recorded is
therefore *narrower* than before, not wider: telemetry reaches a second store
and a second UI, and the words reach neither.

**Logs are bridged at INFO and above, and the level is the control.**
`log_external_request`/`log_external_response` put `/discover` and `/select`
bodies in the log at DEBUG, clipped at 8000 characters but otherwise verbatim.
Those bodies are the one place outside the `gen_ai.*` attributes where a
farmer's words appear. The bridge's handler sits at INFO, so
`DSS_LOG_LEVEL=DEBUG` still prints them to stderr for whoever is debugging and
still exports none of them.

Filtering in the process rather than at the collector is deliberate. Leaking
first and filtering downstream leaves the content one forgotten processor away
from being stored, and the forgetting would be silent.

**Metrics are on by default, and delta.** `OTEL_METRICS_EXPORTER` flips from
`none` to `otlp`: the reason for `none` was that the endpoint was Langfuse, and
the endpoint is no longer Langfuse. `OTEL_EXPORTER_OTLP_ENDPOINT` now defaults
to the collector for the same reason — it is in the same compose file, so it is
always there. Temporality is **delta**, not OTLP's default of cumulative:
ClickHouse stores what it is given, so a cumulative counter makes every rate
panel compute a windowed difference in SQL, where delta lets a panel sum rows.

**The DSS gets a name.** Nothing set `OTEL_SERVICE_NAME`, so spans arrived as
`unknown_service`. In a ClickHouse other services also write to, that is not
cosmetic — `service.name` is the only thing separating our rows from theirs.
`deployment.environment.name` joins it, or one Grafana cannot tell a staging
turn from a production one. Every panel filters on both.

**Langfuse's credentials move to the collector.** The DSS stops setting
`OTEL_EXPORTER_OTLP_HEADERS` entirely — the collector is what talks to
Langfuse, so it is what holds the key. This also retires a trap: that variable
is a URL-encoded list, so the header needed `%20` rather than a space, and
getting it wrong 401'd every batch while the turn answered normally.

**The database is shared, and that was checked rather than assumed.** The
collector writes to `otel` on the existing server. Another collector already
writes there, so ours would insert into its tables and a schema mismatch would
fail at runtime in production with no test that could have caught it.
Verifying the existing DDL is a step before deployment, not an assumption; if
the schemas differ, the fallback is a database of our own.

**Retention is 30 days**, the window ADR-0007 chose, applied as a table TTL by
the exporter at creation. One window for both stores is easier to state and
easier to defend than two.

**The dashboard is checked in.** `observability/stages.py` already calls its
stage names "a dashboard contract". A contract is only enforced if breaking it
breaks something visible, so the dashboard lives in the repo and is
provisioned from disk, with UI edits disabled.

**ClickHouse and Grafana are not run by this repo.** They already exist. The
collector reaches them over `oan-edge`, the external network Langfuse's compose
file already declares — now declared here too, rather than asserted in a
comment.

## 5. Consequences

**Good.** The metrics published for #139 are finally visible. A turn's
numbers, its spans and its log lines are in one store, joined on `trace_id`
without a search. Where telemetry goes is a YAML file and a restart.

**Good.** The §6.2 gap is half closed, and the half that closed is the one
facing the wider audience — Grafana has more viewers than Langfuse.

**Bad.** A second process in the path. If the collector is down the DSS drops
its telemetry after retrying; turns are unaffected, but the gap is invisible
until someone looks at a graph. Retry and queue are left at their defaults —
in memory, drop on overflow — because a disk buffer trades lost telemetry for
a disk-full failure that takes the collector down with it.

**Bad.** The strip list is a copy of attribute names Pydantic AI chose. A
version bump that renames one sends content to ClickHouse, and nothing fails
loudly. The list names its instrumentation version so a reader knows what to
re-check.

**Bad.** Two collector configs — one for a deployment, one for a laptop — kept
in step by hand. The collector validates exporters at startup, so an exporter
pointed at a ClickHouse that is not there stops the process rather than
degrading; "ClickHouse is optional locally" cannot be one file.

**Neutral.** The log bridge makes `opentelemetry-sdk` and the OTLP exporter
direct dependencies rather than things inherited from `logfire`. They were
always installed; now they are named.
