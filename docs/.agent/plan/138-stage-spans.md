# Time each stage of a turn, where a tool can read it

Issue: [#138](https://github.com/OpenAgriNet/engineering-tracker/issues/138) · Branch: `feat/138-stage-spans`

## Context

A turn opens exactly one span today — `dss.turn`. Everything else in the trace
comes free from Pydantic AI, which spans its own model calls. So the four model
stages are timed and nothing else is.

The stages are not unmarked. `trace_component()` already brackets six of them and
logs `event=enter` / `event=exit` with elapsed milliseconds. The timing exists.
It is in a log line, so nothing can group it, graph it, or compare two runs of it.

Two stages are not marked at all: the provider discovery fan-out and the outbound
provider calls. Both are network work, both are likely slow.

One number is missing entirely: when the farmer first heard anything. A turn
streams, so an answer starts arriving long before the turn ends. ADR-0011
measured a composer span at 4.22s of which 4.03s was waiting for the first token
— almost all thinking, almost no writing. Nothing records that split.

The outcome: when a turn is slow, say which stage was slow without reading log
files line by line.

## Decisions taken

The first one gets an ADR — it constrains every future core service, not just
this change, and `docs/.agent/plan/README.md` says decisions with real trade-offs
belong in `docs/ADR/` rather than in a plan that gets deleted. The rest are scoped
to this change and live here.

**Tracing stays in the application boundary.** No tracing port, no span code in
`core/`. This is the load-bearing decision and it constrains everything below.

→ **New ADR: `docs/ADR/0012-tracing-stays-in-the-application-boundary.md`.**
Options weighed: a `Tracer` Protocol under `ports/` with an OpenTelemetry adapter;
wrapping from the orchestration side; letting `core/` import `dss.observability`
directly. Chosen: wrap from orchestration. The cost is per-slot discovery spans,
and the next story's per-provider duration metric answers "which provider is
slow" better than a span would anyway. Worth noting in the ADR:
`adapters/observability/tracing.py:138-140` already predicted the port, and
`tests/unit/test_core_isolation.py` does not currently ban `opentelemetry` inside
`core/` — the rule holds by convention, not by test. This story closes that gap
so the ADR is enforced rather than merely stated.

**`trace_component` gets a registered span opener.** It holds a slot. `create_app`
fills it after `configure_tracing()`. Unfilled means no span — which is exactly
the behaviour wanted when no OTLP endpoint is configured. `trace_log.py` imports
nothing from `adapters/`; the dependency arrives at startup instead.

**The slot holds a span opener, not a generic wrapper.** `trace_component` builds
`dss.stage.<component>` itself, so the naming rule lives in one line next to the
component name it is built from. The next story's metric labels derive from these
names, so the naming is a contract.

**The discovery fan-out is wrapped from orchestration.** One span for the whole
fan-out, no per-slot spans. The slot tasks are spawned inside `core/`, so
per-slot detail would require a port we chose not to build. A per-provider
duration metric in the next story answers "which provider is slow" better than a
span does anyway — spans show one turn, metrics show the pattern.

**`/select` gets an outer span and a child per attempt.** The retry loop is one of
the few things here that can quietly turn a fast call into a slow one. The outer
span gives total cost of reaching the provider; the children show whether that
cost was one slow response or three failures with waits between them.

**`/discover` gets one span, not two.** The two clients are built differently.
Select retries — three attempts with exponential backoff — so it has an attempt
dimension worth nesting. Discovery makes exactly one POST and never retries; a
second level would be a parent with exactly one child, always. Same empty nesting
we rejected for `dss.plan_execution`.

**The discovery span sets its own status from the returned result.** This is the
part that is easy to get wrong. Select *raises* on failure, so a span around it
fails visibly with no extra work. Discovery deliberately *never* raises — an
exception would cancel every sibling query in the caller's task group
(`adapters/discovery/client.py:75-77`), so failures come back as data inside a
`DiscoveryResult`. Left alone, a provider that timed out produces a green span
indistinguishable from one that answered. The span must read the returned
`failures` and mark itself accordingly.

**No `dss.plan_execution` span.** Every provider call already happens inside the
planner block, so the span would start and end at the same instants as
`dss.stage.planner` — a box with an identical box inside it. Planner duration
comes from the planner span, select duration from the select spans. Both metrics
the next story wants are available without it.

**No planner iteration count on the turn span.** Pydantic AI already spans both
numbers. Select calls: one `execute_tool select` span per call, carrying
`gen_ai.tool.name = "select"` and `gen_ai.agent.name = "planner"`. Model
requests: one `chat <model>` span per request, tagged with the same agent name.
Counting spans in a trace answers both. Putting them on the turn span as numbers
would save a dashboard one subquery, at the cost of widening the `Plan` protocol,
the closure, the orchestrator call site, and every test fake — the planner
returns bare `Evidence` today and `Evidence` is frozen and closed. Not worth it.

## Amendments to the issue

Two acceptance criteria get struck from the issue body before work starts:

- *"Each parallel discovery slot opens its own child span"* — needs a tracing
  port in `core/`, deliberately not built. **Mostly satisfied anyway:** each slot
  makes exactly one call into the discovery adapter, and that call gets a
  `dss.discover` span. So one span per provider query appears regardless, and a
  slow single provider is visible. What is lost is only the slot's own bookkeeping
  time around that call, which is in-memory and negligible.
- *"Plan execution opens a span covering all provider calls in one turn"* —
  would duplicate `dss.stage.planner` exactly.
- *"The `dss.turn` root carries how many iterations the planner ran"* — already
  countable from Pydantic AI's own `execute_tool select` and `chat` spans.

## What gets built

### 1. The six stage spans

`trace_component` in `src/dss/observability/trace_log.py` opens a span named
`dss.stage.<component>` around its existing work, via the registered opener. Its
log lines are unchanged. All six call sites — `orchestration/turn.py:159,161,163,170`
and `orchestration/orchestrator.py:226,234` — are untouched.

A stage that raises leaves a span with status `ERROR` and a recorded exception,
then re-raises unchanged. `trace_component` already catches `BaseException` and
re-raises; the span work goes in the same place.

The four stages that use a model also carry their model name.

### 2. Registration at startup

`src/dss/entrypoint/app.py`, immediately after `configure_tracing()` at line 104
— process start, before any turn, and outside `build_app` so test-constructed
apps are unaffected. Registers the span opener and the four model names from
`Settings`.

### 3. The discovery fan-out span

A `with` block in `orchestration/` around the call into
`core/provider_discovery/service.py`. Nothing in `core/` changes.

### 4. The outbound call spans

`src/dss/adapters/invocation/client.py` — `dss.select` on `select` (the retry
wrapper) and `dss.select.attempt` on `_select_once` (one network attempt).
Failures raise `SelectFailed`, so the span fails naturally.

`src/dss/adapters/discovery/client.py` — `dss.discover` on `discover`, one span.
Because this client returns failures instead of raising, the span must set its
own status from the result at the three failure returns: HTTP status error
(`client.py:335`), transport error (`client.py:342`), malformed body
(`client.py:346`). The malformed path currently writes no log line at all, which
makes the span the only signal there.

Both adapters already write request and response log lines; these keep working.

Not instrumented: `adapters/llm/pydantic_ai_provider.py` makes the only other
outbound call, and Pydantic AI already spans it with token counts. Assumption 1
on the issue says not to duplicate those.

### 5. Close the isolation-test gap

`tests/unit/test_core_isolation.py` bans `time`, `logging`, `os`, `httpx` and the
outward packages inside `core/` and `ports/` — but not `opentelemetry`. An
OpenTelemetry import in `core/` passes today. ADR-0012's whole point is that it
should not, so add it to the banned list and make the decision self-defending
against exactly the shortcut the ADR rejects.

### 6. Turn-span attributes

`src/dss/adapters/observability/tracing.py:119-152` — `turn_span` currently takes
three fixed kwargs and sets three attributes. It needs a way to set attributes
after the span opens, because `status` and the timings are only known at the end.

Attributes added to `dss.turn`:

| Attribute | Set when | Absent when |
|---|---|---|
| `status` | turn ends | never (always known) |
| `first_delta_ms` | first `ClaimDelta` yielded | composer never ran, or model returned nothing |
| `first_claim_ms` | first `Claim` yielded | composer never ran, or composer crashed mid-write |
| the four model names | span opens | never |

Each timing also records a span event at the moment it happens, so the instant is
visible on the trace timeline as well as readable as a number.

**Absent, never zero.** Four outcomes never reach the composer — refused,
needs-input, no-match, and an early crash. Those turns produce no claim and no
delta. Setting zero would make "refused in 40ms" indistinguishable from "answered
instantly" and would drag any average down by exactly the refusal rate.

`first_delta_ms` and `first_claim_ms` go missing on *different* turns, which is
why they are two fields: a mid-write crash has a delta and no claim; an empty
model response has a claim and no delta.

## Verification

- Tier 1 — `trace_component` against an in-memory span exporter: the six names,
  `ERROR` status on raise, nothing at all when the slot is unfilled.
- Tier 2 — the select spans in the invocation adapter, outer and per-attempt.
- Tier 3 — one full turn, asserting the tree shape: `dss.turn` → stage spans →
  discovery span and select spans nested correctly. Parenting across the `anyio`
  fan-out is the one thing only an integration test proves.
- Tier 3 — the turn attributes on each outcome: answered (both timings present),
  refused (both absent), and a composer failure (delta present, claim absent).
- Tier 2 — the `dss.discover` span marked failed on a returned failure, not just
  on an exception. This is the one an ordinary span would get wrong.
- `tests/unit/test_core_isolation.py` stays green, now with `opentelemetry` added
  to its banned list.
- With no `OTEL_EXPORTER_OTLP_ENDPOINT`, the full suite passes unchanged and
  nothing warns.

`tests/integration/adapters/observability/test_span_content.py` already uses
`InMemorySpanExporter` with its own `TracerProvider` — reuse that fixture pattern.

## What the trace looks like after this

Today there is no stage level at all — `trace_component` opens no span, so
Pydantic AI's agent spans hang straight off `dss.turn`. This story inserts the
stage level, and the existing spans nest one deeper:

```
dss.turn                        status, model names, first_delta_ms, first_claim_ms
├── dss.stage.intent            intent_model
│   └── invoke_agent intent-classifier      (Pydantic AI, already there)
├── dss.stage.moderation        moderation_model
├── dss.stage.discovery
│   └── dss.provider_discovery              the fan-out, one span
│       └── dss.discover                    one per provider query, no retry level
├── dss.stage.planner           planner_model
│   └── invoke_agent planner                (Pydantic AI, already there)
│       ├── chat <model>                    count these = model requests
│       ├── execute_tool select             count these = select calls
│       └── dss.select                      our span, one per provider call
│           └── dss.select.attempt          one per network attempt
└── dss.stage.composer          composer_model
    └── invoke_agent composer               (Pydantic AI, already there)
```

## Noted, not acted on

The composer is skipped on refused / no-match / needs-input turns; those farmers
get fixed English strings (`no_match_answer()`, `needs_district_answer()`, the
refusal messages) rather than composed text. That is fine for tracing — it just
means both timing attributes are absent there — but it means those messages do
not translate. Worth its own story. The stage spans will show how often those
paths are taken, which is evidence for deciding.

## Sequencing

PR #71 (`feat/122-stream-composer-response`) is open and merges soon. It does not
touch `trace_log.py`, `tracing.py`, `provider_discovery/service.py`, or
`invocation/client.py`. Its one overlap is the composer block in
`orchestrator.py`, which it rewrites to stream — and where `ClaimDelta` is
yielded, which `first_delta_ms` depends on.

Rebase onto `main` after #71 merges before touching the composer block.
