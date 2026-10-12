---
name: review
description: Full review of a diff, branch, PR, or path in this repo — a design pass (SOLID, YAGNI, DRY, naming, primitive obsession, smells, comments, layer boundaries, test tiers) and a correctness pass (bugs, paths and packaging, PII and prompt injection, speed, observability, templates, conventions), run in parallel and merged into one report. Use when asked to review code or a PR, before opening a PR, or when refactoring.
---

# Review

Two passes run in parallel, then one report. The design rules come from the
`writing-code` skill; the checklists are `design.md` and `correctness.md`
next to this file.

## 1. Scope
- No argument → `git diff main...HEAD` plus uncommitted changes.
- A PR number → `gh pr view <n>` for the body, `gh pr diff <n>` for the diff.
- A branch or path → review that.
- Review changed lines and the code they directly touch. Don't audit the repo.
- Changed files include `src/`, `tests/`, `prompts/`, `configs/`, docs and
  container files. Templates and config are code here.
- Large diff (more than ~20 files): read `src/` first, then templates and
  config, then tests by tier.
- Cite line numbers from the branch side of the diff.

## 2. Run both passes in parallel
Launch two subagents in one message. Give each the scope (diff command and PR
body) and one checklist path:

- **Design** — `.claude/skills/review/design.md`, plus `writing-code`.
- **Correctness** — `.claude/skills/review/correctness.md`.

Each returns findings as: `file:line`, severity, summary, failure scenario
(correctness) or reason (design), fix. Each must not edit files or post
anything.

## 3. Merge
- Drop duplicates. If both passes found the same thing, keep one and tag both.
- Group findings that share a fix.

## 4. Check intent before reporting
For every finding, look for a reason the code is deliberate:
- comments next to the code, and the config file it reads;
- `docs/ADR/`, `docs/DSS_ARCHITECTURE.md` (§8 open items), `CONVENTIONS.md`;
- the PR body.

If the code is explained as deliberate, drop the finding. If the decision is
real but not written down where a reader would look, keep it as a
**Consider: document this** note, not a bug.

## 5. Verify
Read the actual lines for every Must fix and confirm the failure scenario
holds. Mark each finding **checked** or **not checked**. Drop any you
disprove.

## 6. Report
One list, grouped by severity. Tag each finding `[bug]`, `[design]` or
`[convention]`.

```
### Must fix
- src/dss/core/channel/localize.py:96 [bug] (checked) — blocks are translated one after another; three blocks can wait ~90s.
  Fix: translate them together in an anyio task group.

### Should fix
- src/dss/core/planner/sufficiency.py:20 [design] — `unserved_asks` is a noun phrase for a function.
  Fix: rename to `find_unserved_asks`.

### Consider
- ...
```

- **Must fix** — wrong behaviour a farmer or operator would see, PII leaks,
  service fails to start, boundary breaks, positional multi-type tuples in new
  code, wrong test tier.
- **Should fix** — latent bugs needing unusual input, observability gaps,
  primitive obsession on a port, other smells, naming, SOLID breaks,
  convention misses.
- **Consider** — comment trimming, style, "document this" notes.

Rules for the report:
- Cite `file:line`. Quote at most one line of code.
- No finding without a concrete fix.
- Don't flag code the diff didn't touch, unless the change makes it worse.
- If nothing is wrong, say so in one line.
- Don't edit files or post comments unless asked.
