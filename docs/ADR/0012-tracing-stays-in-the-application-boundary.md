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
dss.turn                        status, model names, first_delta_ms, composed_ms
├── dss.stage.intent
├── dss.stage.enrichment
├── dss.stage.moderation
├── dss.stage.discovery         asks_queried, asks_failed
│   └── dss.discover            one per provider query
├── dss.stage.planner
│   └── dss.select              one per provider call
│       └── dss.select.attempt  one per network attempt
└── dss.stage.composer
```

**`trace_component` opens its span through a slot.** `observability/trace_log.py`
holds a `StageSpanOpener | None` and imports nothing from `adapters/`.
`configure_telemetry()` fills it at startup, so spans and the exporter cannot be
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

**Partial failure is a count, not a status.** `dss.stage.discovery` stays green
when one provider fails. The other asks still answer and the turn still
succeeds. Marking it `ERROR` would make every error-rate panel measure provider
flakiness instead of real failures. OpenTelemetry status is only OK or ERROR, so
"one of three failed" is reported as `asks_queried` and `asks_failed` — numbers
that can be filtered and graphed.

The counts go **on the stage span**, not on a span of their own.
`trace_component("discovery")` brackets exactly the fan-out call, so a span
there would start and end with it — the same empty nesting rejected below for
`dss.plan_execution`. `core/` may not open a span itself, so `orchestration/`
reads the result and calls `set_current_span_attributes`.

**A failure is recorded by type, never by message.** OpenTelemetry would put
`exception.message` and a full `exception.stacktrace` on a span by default, and
exception text here is not safe to export: `SelectFailed` embeds the provider's
entire response body, which echoes the farmer's query back. §6.1 names traces as
a place personal data may not reach, and there is no length clip as there is on
the log path. `open_span` therefore turns OpenTelemetry's own recording off and
sets `ERROR` with the exception type alone.

It also catches `BaseException`, not just `Exception`. OpenTelemetry ignores
anything deriving straight from `BaseException`, so a cancelled turn would close
green — and cancellation is how a turn ends when the farmer closes the screen
mid-answer, which is exactly a case worth seeing.

**No `dss.plan_execution` span.** Every provider call already runs inside the
planner block, so it would start and end with `dss.stage.planner`.

**The turn root carries what only the turn knows.** `turn_span` yields a
`TurnRecorder`, because `status` and the two timings are known only while the
turn runs. `first_delta_ms` is when the farmer heard the first word;
`composed_ms` is when the answer is fully written, sources attached. Both also record
a span event, so the moment is on the timeline as well as readable as a number.

They are two fields because they go missing on different turns: a composer that
fails mid-write has a delta and is never composed. **Absent, never zero** — refused,
needs-input and no-match turns never reach the composer, and zero would make
"refused in 40ms" look like "answered instantly".

`composed_ms` is not a mid-stream moment. A claim carries its sources, and
sources are resolved from the complete text, so no claim can exist until the
last delta has arrived. It was named `first_claim_ms` until #139; renamed
because that name read as time to first word. Read the two together: `first_delta_ms` is how
long the farmer waited to see anything, and the gap is how long the writing took.

`status` is always present, including on a turn that crashed or was abandoned —
those get `error`. Leaving it off would drop exactly those turns out of any
breakdown by status, which is where they most need to appear.

The four model names go on the same span. They arrive through
`configure_telemetry()` with everything else rather than through a second call,
so tracing cannot be on with the names left behind.

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
- `open_span` yields the raw span, so its callers touch OpenTelemetry types
  directly. Allowed where they are, but a wider surface than yielding nothing.
  `turn_span` yields a `TurnRecorder` instead, which is the better shape — one
  verb per fact, and no SDK type in `orchestration/`. `open_span` should follow
  if a third caller needs more than a plain `with`.

## 6. Metrics ride the same seam

Added when metrics were built (#139), because they were decided by this ADR
rather than by one of their own.

Metrics are the other half of the same picture: a span says why *this* turn was
slow, a metric says whether this week is slower than last. They raised the same
question — where may the code live — and got the same answer.

- **A second slot, beside the first.** `trace_log.py` now holds a
  `StageMetricRecorder | None` as well, filled by the same startup call. A
  stage cannot be spanned but unmeasured, or the reverse.
- **Still no port.** Option A was rejected here for want of a second
  implementation, and metrics do not supply one. If a second backend ever
  arrives it should supersede this ADR, not bend it.
- **`configure_tracing` became `configure_telemetry`**, since it now wires both.
- **Stage names are a published contract**, not a convention. Span names and
  metric labels derive from the same values, so a dashboard joins the two by
  that string. They are now a `Stage` enum in `observability/stages.py` — the
  one table this ADR recorded as missing. Renaming a member is a breaking
  change for anything graphing it.
- **Labels are bounded, and a test holds us to it.** Every combination of label
  values is its own time series, and an unbounded label (a provider id, a
  district, a farmer id) degrades the backend rather than failing a build.
  `LABEL_KEYS` names what is allowed.

---

**Revisit when** a core service's own behaviour needs tracing — not when one
call site wants more detail. Then Option A's cost buys something, and this ADR
should be superseded rather than bent.
