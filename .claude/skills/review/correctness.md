# Correctness pass

Finds code that does the wrong thing. Design and naming belong to the design
pass, so skip them.

For each check, trace a real input through the changed code. Every finding
needs a failure scenario: what goes in, and what wrong thing comes out. If
you can't write one, it's not a finding.

## Checks, in order

### Behaviour
- A fallback or broad `except` that hides a code bug, not just an expected
  failure (a missing template value served as "English instead").
- Two parts of one turn decide the same thing separately and can disagree
  (prompt and skills resolving language on their own).
- A new branch or exit misses a step the other exits do (translation,
  redaction reveal, telemetry note, recorder call).
- Off-by-one, empty input, `None`, an empty string after `.strip()`, a
  language tag with a region (`hi-IN`).
- A changed function signature with a caller left on the old shape.

### Paths, config and packaging
- A relative path that only works from the repo root.
- Runtime files outside `src/dss` (the wheel ships only `src/dss`). Check that
  `Dockerfile` / compose copy them.
- A new setting without a default, or with a default set in two places.
- A setting the container never receives (`docker-compose*.yml`, env files).

### Security and PII
- Text the farmer typed reaching a prompt marked as trusted or system text —
  including inside code-written messages (`clarification-text.yaml` fills in
  place names).
- Redaction tags (`«…»`) passing through any model call after redaction. They
  must come back unchanged, or `Visibility.reveal` misses them.
- A provider request or response body logged above DEBUG (ADR-0014).
- Personal data in a span attribute, metric label or log field.

### Speed
- Model or network calls in a loop, one after another, where they could run
  together (anyio task group) or as one call.
- Timeouts that multiply: N calls × the per-call timeout on a path the farmer
  waits on.
- A path that used to make no model call and now does.

### Observability contract
- A new model call with no stage in `observability/stages.py`, or one that
  reuses another component's client and is counted as that component.
- A new trace event that fires more than once per turn for one fact.
- Stage or metric names changed without `grafana/dashboards/dss.json`.

### Prompts and templates (`prompts/**`, `configs/prompts.yaml`)
- A placeholder declared but unused, or used but not declared.
- Language versions of one prompt that drift apart in rules, not just wording.
- Quoting or escaping that breaks on real values (apostrophes, braces, newlines).
- User-supplied values not marked as data.

### Conventions and docs
- A log without `request_id` (`CONVENTIONS.md`).
- PR body without `Relates to #N`; changelog entries without `(#N)`.
- A design decision with no ADR, or `docs/DSS_ARCHITECTURE.md` not updated.
- References to renamed or deleted files, functions or settings left in code,
  docs or tests.
