# ADR-0006: `Skill` gains `tool_names` for declarative tool gating

- **Status:** ACCEPTED
- **Date:** 2026-09-04
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

The design doc defines `Skill(id, domain, guidance)`. It does not say which
tools a skill turns on.

Skill *selection* — picking which skills apply to a turn — is still [Open]
in the design. The POC skips it: one skill, `provider-invocation`, is always
on. But even with one skill, the agent still needs to know which tools to
offer the model.

An existing adopter solves this with a `prepare=` hook per tool: code that
runs at call time and decides whether to include that tool. It works, but
the fact "this tool belongs to this skill" lives inside a callback, not on
the `Skill` itself.

## 2. Decision Drivers

1. **State it on the data, not in a callback.** A skill's tools should be
   readable by looking at the `Skill`, not by reading hook code.
2. **A tool from an unselected skill must be truly absent** from the model's
   tool list — not shown and then filtered.
3. **Don't wait for skill selection to be designed.** Gating must work today,
   with one skill.

## 3. Considered Options

- **Option A — add `Skill.tool_names: tuple[str, ...]`.** Tools bind from the
  union of every selected skill's `tool_names`.
- **Option B — keep `prepare=` hooks, one per tool.** Same pattern an
  existing adopter already uses.
- **Option C — a separate table mapping skill id to tool names.** Keep
  `Skill` exactly as the design doc has it; store the mapping elsewhere.

## 4. Decision Outcome

**Chosen option: A.**

```python
class Skill:
    id: str
    domain: str
    description: str              # what this skill is for — read by future selection
    guidance: str                 # goes into the model's prompt
    tool_names: tuple[str, ...]   # which tools bind when this skill is selected
```

This adds `tool_names` to the design doc's `Skill`. Tools bind from the union
of every selected skill's `tool_names`. An unselected skill's tools never
reach the model — they are not registered on the agent at all, so there is
nothing to filter later.

`description` and `guidance` stay two separate fields. `description` explains
the skill, for future selection code to read. `guidance` is text for the
model itself, added to the prompt once the skill is selected. Two different
readers, so one field would mix them up.

Today there is one skill: `provider-invocation`. It carries the tools needed
to read a `ProviderCapability`, build `resourceAttributes`, and call
`/select`. We wire the gating now, while there is one skill, because getting
it wrong costs little at this size.

### 4.1 Positive Consequences

- Which tools a skill enables is visible on the `Skill` itself — no need to
  read hook code to find out.
- Adding a second skill later is additive: give it its own `tool_names`. The
  binding code, which already unions across skills, does not change.
- Matches the project's preference for declarative code over imperative
  hooks.

### 4.2 Negative Consequences

- This is a change from the design doc's `Skill(id, domain, guidance)`. This
  ADR records that change; `docs/dss-design-v2.md` is updated in the same
  commit.
- `tool_names` refers to tools that must exist and be registered elsewhere
  (`orchestration/planner.py`). A typo here is a silent runtime gap, not a
  type error. Fine with one skill; worth a startup check if skills grow.
- Every tool must belong to a skill to ever bind — this ADR gives no other
  way for a tool to reach the model. A tool that should always be available,
  no matter which skill is selected, has no place to attach yet. See the
  revisit trigger below.

## 5. Rejection Rationale

**Option B** (`prepare=` per tool) is what an existing adopter does today and
it works. But the fact "this tool belongs to this skill" is spread across
each tool's own hook instead of living in one place. That gets harder to check as skills
grow.

**Option C** (separate table) keeps `Skill` matching the design doc, but now
the same fact — "this skill brings these tools" — lives in two places that
must be kept in sync by hand. `tool_names` on `Skill` keeps it in one place.

## 6. Revisit Triggers

- Skill selection gets designed (currently [Open] in the design doc). Check
  that unioning `tool_names` across selected skills still makes sense once
  more than one skill can be active.
- The number of skills grows enough that a typo'd `tool_names` entry is worth
  catching at startup instead of at first use.
- A tool needs to be available no matter which skill is selected. Add a
  separate always-on tool list alongside the skill-gated ones then. Not built
  now, because no such tool exists yet.

## 7. Follow-up Actions

- **[DSS code owners]** Update `docs/dss-design-v2.md`'s `Skill` definition to
  include `tool_names`, noting this ADR.
- **[DSS code owners]** Implement the `provider-invocation` skill and its
  tool binding in the planner POC (issue #10).

## 8. Notes

- Full context: `docs/.agent/plan/10-planner-agent-poc.md`, section "A
  `Skill` knows its tools."
