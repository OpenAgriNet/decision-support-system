# ADR-0007: Langfuse for LLM Tracing

- **Status:** ACCEPTED
- **Date:** 2026-09-11
- **Deciders:** OAN (OpenAgriNet) DPG architecture group
- **Consulted:** DSS engineering
- **Informed:** Adopter engineering teams; OAN DPG steward

---

## 1. Context and Problem Statement

A turn runs four agents (intent, moderation, planner, composer) plus discovery
and invocation calls. When one produces a wrong answer, the log tells you the
shape of what happened — which component ran, how long it took, what status
came back — and nothing about *why* the model decided what it decided. The
planner in particular loops: it calls `describe_capability`, reads a schema,
and decides what to pass to `select`. That loop is where turns go wrong, and it
is invisible.

`Agent.instrument_all()` has been in place since tracing was first wired: every
Pydantic AI agent run already emits OpenTelemetry spans, with token counts,
tool names, latency and errors. What was missing was somewhere for them to go,
and a way to find one turn's spans among everyone's.

**The question.** Where do the DSS's traces land, and how does an operator
holding a `transactionId` from a complaint get to the spans for that turn?

## 2. Decision Drivers

- **Filterable by the contract's own ids.** Support holds a `transactionId`,
  `messageId` and `sessionId` (§5.1); the trace store must be searchable by all
  three, not only by a store-internal id.
- **Content visibility.** A trace without the prompt and the completion answers
  "which agent was slow", not "why did it say that". The second question is the
  one that motivated this.
- **Data stays in the adopter's account.** §6.1 constrains where personal data
  may be persisted. A third-party SaaS trace store is a separate processor with
  its own declaration obligations.
- **Vendor neutrality.** The DPG's interoperability principle (ADR-0001) applies
  to observability too: an adopter must be able to run something else.

## 3. Considered Options

**A. Langfuse Cloud.** Zero infrastructure — an endpoint and a key pair. Every
trace, including the farmer's words, leaves the adopter's account and lands with
a third party, which §6.1 treats as an explicit personal-data processor
requiring separate declaration. Rejected on that alone.

**B. Langfuse v2.** Two components — web plus Postgres. Superseded by v3 in
December 2024; no architecture work since, and no stated support line. Adopting
it now schedules a migration. Rejected.

**C. Arize Phoenix.** One container, SQLite by default, OTLP-native — roughly a
sixth of the infrastructure, and it satisfies the filtering requirement.
Rejected for what it lacks rather than what it costs: prompt management, a
maturing annotation/evaluation surface, and multi-project access control, all of
which the DSS expects to need as adopters extend it. Kept on file as the
fallback if the operational cost of D proves not to be worth it.

**D. Self-hosted Langfuse v4.** Six components — `langfuse-web`,
`langfuse-worker`, Postgres, ClickHouse, Redis/Valkey, and an S3-compatible blob
store. All six are required; the vendor documents no reduced configuration.

## 4. Decision Outcome

**Option D: self-hosted Langfuse v4**, reached over OTLP.

**Transport is OTLP, not a Langfuse SDK.** Nothing in the DSS imports
`langfuse`. Traces go wherever `OTEL_EXPORTER_OTLP_ENDPOINT` points, so
substituting Phoenix, a Collector, or anything else is a configuration change
with no code change. This is what satisfies the neutrality driver: the seam is
the protocol, not an adapter wrapping a vendor client.

**Blob storage is MinIO, not S3.** Langfuse speaks the S3 API either way, so
moving to a managed bucket later is an endpoint and two credentials. Running
MinIO keeps a deployment identical to a laptop, which is worth more at this
stage than managed durability.

**The four ids.** `traceId` and `spanId` are OpenTelemetry's own, on every span.
`sessionId` is set as `langfuse.session.id` on the turn's root span, which
Langfuse promotes to a first-class Session. `transactionId` and `messageId` have
no native Langfuse field and are set as trace metadata.

Note what this means for `traceId`: the contract says the caller's
`transactionId` **is** the `traceId` (§5.1), but Langfuse files traces under the
W3C id OpenTelemetry mints. Deriving the W3C id from `transactionId` was
considered and rejected — `transactionId` is typed `str`, not `UUID`, so a
caller may send something that is not 32 hex characters, and a scheme that works
for well-behaved callers and silently hashes for the rest is worse than two ids
that are each unambiguous. An operator therefore searches metadata for a
`transactionId`, and the DSS writes the OpenTelemetry trace id into every
`dss.trace` log line so the two join by grep.

**Message content is recorded, without redaction.** See §5.

**Retention is 30 days**, applied as a ClickHouse TTL and a matching MinIO
lifecycle rule, both from one idempotent script with the window as a variable.

Retention is doing real work here, not just bounding disk — it is one of the
four things standing in for the redaction interceptor (§5). 30 days is long
enough to investigate a complaint that took a week to reach anyone, and it is
30 days of farmers' words rather than 5. That trade was made deliberately in
review; a deployment that wants the tighter bound sets `RETENTION_DAYS=5`.

## 5. PII posture: an explicit, bounded deviation from §6.1

`DSS_TRACE_INCLUDE_MESSAGE_CONTENT=true` puts the farmer's query and the
composed answer verbatim into spans. §6.1 states that personal payloads are not
inserted into "prompts, tool registries, shared context stores, logs, traces, or
analytics", and §6.2 requires a sink-layer redaction interceptor that §8 records
as unbuilt. **This deployment does not comply with §6.2, and the deviation is
recorded here rather than left implicit.**

What bounds it:

- **The store is inside the adopter's account.** No third-party processor.
- **Retention is 30 days** in both ClickHouse and the blob store, and a
  deployment can shorten it with one variable.
- **Access is not public.** Langfuse's own authentication, and the host is not
  open to the internet.
- **It is one environment variable.** Only a literal `"true"` opts in; unset is
  off, and the process logs a warning at startup when it is on.

What is not resolved: §6.1 is titled "Needs more discussions" and §8 carries the
DSS envelope/forwarding question as open. This ADR does not close either. It
records that, with those questions open, the deployment chose visibility over
the unbuilt control, and names the conditions under which that was acceptable.

**When §6.2's redaction interceptor is built, it belongs in front of this
exporter** — as a span processor, which is the sink layer §6.2 names — and this
section should be revisited rather than quietly outlived.

## 6. Consequences

**Good.** A turn is one trace: root span, four named agent spans, the planner's
tool calls, and a generation under each with prompt, completion and token
counts. Latency attribution is immediate — the planner is typically half a
turn's wall-clock, and its tools return in single-digit milliseconds, so the
cost is model round-trips rather than providers. Log lines and spans join on
`trace_id` without a search.

**Bad.** Six containers to run, of which ClickHouse is the one that will
eventually need attention. The vendor describes its own compose file as suited
to "a single VM without high availability, scaling, or backups", which is what
this deployment is.

**Bad.** Traces carry personal data for 30 days, under §5's conditions. The
control that was supposed to prevent this does not exist yet, and 30 days is
six times the window first proposed.

**Neutral.** `logfire` remains a dependency for its Pydantic AI instrumentation
with `send_to_logfire=False`. Its default scrubber is disabled: it redacts
attributes whose key matches a sensitive-name pattern, and `langfuse.session.id`
matched on "session", so the id arrived as the literal string
`[Scrubbed due to 'session']`. With message content already recorded, a scrubber
over the id attributes was protecting nothing.
