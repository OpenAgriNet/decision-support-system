# ADR-0012: tracing stays in the application boundary

- **Status:** ACCEPTED
- **Date:** 2026-09-21
- **Deciders:** DSS code owners

---

## 1. Context and Problem Statement

ADR-0007 gave a turn one span, `dss.turn`. Pydantic AI spans its own model
calls. So the four model stages are timed and nothing else is.

`trace_component()` already brackets six stages and logs the elapsed time. The
timing exists, but it is in a log line. Nothing can group it or graph it. Two
stages are not marked at all: the discovery fan-out and the outbound provider
calls. Both are network work.

Adding spans raises one question everything else depends on: **where may span
code live?**

`core/` imports nothing outward. A span needs a tracer, which needs
process-global state set at startup. But the stages worth timing are in
`core/`.

## 2. Decision Drivers

1. `core/` must be testable without infrastructure. That is the only reason to
   pay for ports-and-adapters.
2. A framework swap must not touch `core/` (ADR-0001).
3. Timing a stage does not require being inside it.
4. Do not build a port before there is a second implementation.

## 3. Considered Options

**A. A `Tracer` port with an OpenTelemetry adapter.** The shape the repo
reaches for elsewhere. It buys per-slot discovery spans. It costs a protocol,
an adapter, a no-op fake in every core test on a traced path, and a tracing
argument on core signatures that serves nothing else. Rejected against driver 4.

**B. Let `core/` import the tracer directly.** Cheapest to write. Breaks
drivers 1 and 2, and does it invisibly — no signature changes, so nothing
signals that core now needs global state. Rejected.

**C. Wrap from the orchestration side.** `orchestration/` opens the span around
its call into `core/`. Adapters open their own for outbound calls. `core/` is
untouched. Lost: per-slot discovery spans, because the slots are spawned inside
the core fan-out.

## 4. Decision Outcome

**Option C. No tracing port, no span code in `core/`.**

```
dss.turn
├── dss.stage.intent
├── dss.stage.moderation
├── dss.stage.discovery
│   └── dss.provider_discovery        the fan-out, with counts
│       └── dss.discover              one per provider query
├── dss.stage.planner
│   └── dss.select                    one per provider call
│       └── dss.select.attempt        one per network attempt
└── dss.stage.composer
```

**`trace_component` opens its span through a slot.** `observability/trace_log.py`
holds a `StageSpanOpener | None` and imports nothing from `adapters/`.
`configure_tracing()` fills it at startup, so spans and the exporter cannot be
on independently. An unfilled slot means no span — the right behaviour with no
OTLP endpoint, which is every test and every local run.

**The lost per-slot span costs little.** Each slot makes one call into the
discovery adapter, and that call opens `dss.discover`. So one span per provider
query appears anyway, and a slow provider is visible. Only the slot's in-memory
bookkeeping is missing.

**`/select` gets two levels, `/discover` gets one.** Select retries three times
with backoff, so the outer span is the total cost and the children say whether
that was one slow call or three failures. Discover makes one POST and never
retries; a second level would always be a parent with one child.

**A returned failure marks its own span.** The discovery client never raises —
an exception would cancel every sibling query in the caller's task group — so
failures come back as data. Left alone the span exits green, and a provider that
timed out would look like one that answered. `dss.discover` sets `ERROR` itself
at each failure return.

**Partial failure is a count, not a status.** The fan-out span stays green when
one provider fails. The other asks still answer and the turn still succeeds.
Marking it `ERROR` would make every error-rate panel measure provider flakiness
instead of real failures. OpenTelemetry status is only OK or ERROR, so "one of
three failed" is reported as `asks_queried` and `asks_failed` — numbers that can
be filtered and graphed.

**No `dss.plan_execution` span.** Every provider call already runs inside the
planner block, so it would start and end with `dss.stage.planner`.

**A test enforces this, not convention.** `tests/unit/test_core_isolation.py`
banned `time`, `logging`, `os` and `httpx` in `core/` but not `opentelemetry`.
It is now on that list.

## 5. Consequences

**Good**

- `core/` stays plain Python in, plain Python out.
- "Which stage was slow" is answerable without reading log files.
- Span code stays in `adapters/observability/` and its call sites.
- Turning tracing off turns stage spans off, in one place.

**Bad**

- No per-slot discovery span. Only the call is visible, not the slot around it.
- Span names are strings at call sites, not one table. Dashboards derive from
  them, and nothing stops a typo. `test_turn_span_tree.py` pins the ones that
  matter.
- `open_span` yields the span, so two callers touch OpenTelemetry types
  directly. Both are places where that is allowed, but it is a wider surface
  than yielding nothing.

**Revisit when** a core service's own behaviour needs tracing — not when one
call site wants more detail. Then Option A's cost buys something, and this ADR
should be superseded rather than bent.
