# 0001 — Python scaffolding

**Issue:** #57 · **Status:** in review

## Problem

The repo was documentation-complete and tooling-empty: architecture doc, one
accepted ADR, conventions, a 7-tier testing table — and no executable files at
all. No `pyproject.toml`, no `.py` anywhere (not even `__init__.py`), no CI, no
pre-commit, no changelog.

Nothing from the moderation + intent slice can be built, imported, or tested
until that substrate exists. This change is scaffolding only.

## Scope

**In:** `pyproject.toml`, package layout, the framework-boundary check,
pre-commit, CI, changelog, gitignore.

**Out, deliberately:** the policy schema, the default policy pack, the domain
models, and the loader. Those are the next change, so this one can be reviewed as
tooling rather than tooling-plus-design.

## Decisions

### hatchling, not uv_build

The plan originally specified uv's own build backend. The locally installed uv
(0.7.13) doesn't offer it — `uv init --build-backend` lists only hatch, flit,
pdm, poetry, setuptools, maturin, scikit. hatchling is also the more portable
choice, and it needs `packages = ["src/dss"]` because `src/dss` doesn't match the
distribution name.

### pydantic-ai-slim, not pydantic-ai

The `pydantic-ai` metapackage ships no code of its own; it force-installs
anthropic, google-genai, logfire, mcp, evals, cli, and web whether used or not.
The LLM provider is a swappable adapter behind a port, so extras are declared
explicitly as adapters get built.

### Both framework packages are banned from core/, not just one

`pydantic-ai-slim` pulls **pydantic-graph** in as a *core* dependency, under a
separate top-level import name. Banning only `pydantic_ai` would leave `core/`
free to import the graph library — so both are banned.

Enforced twice, deliberately: ruff's TID251, plus a test that walks the AST of
every module under `core/`. Ruff's own documentation says TID251 "is only meant to
flag accidental uses, and can be circumvented via `eval` or `importlib`" — so on
its own it is a guardrail, not a boundary.

All four cases were verified by planting imports:

| planted | expected | result |
|---|---|---|
| `pydantic_ai` in `core/` | rejected | TID251 fired |
| `pydantic_graph` in `core/` | rejected | TID251 fired |
| `pydantic_ai` in `core/`, via the test | test fails | failed |
| `pydantic_ai` in `orchestration/` | allowed | allowed |

### Types live with the function that owns them

`core/<function>/models.py` alongside `core/<function>/service.py`, with
`core/shared/` for genuinely cross-cutting types. `core/models/` as a single
shared bag was dropped: in a pipeline every stage's output is the next stage's
input, so nearly every type qualifies as "shared" and the bag stops carrying
information.

`models.py` starts as a module and becomes a `models/` package only when it earns
it. A package holding two short files is over-structured.

### Only the packages this slice needs

`core/intent`, `core/moderation`, `core/shared` — not all eight logical functions
the architecture names. The architecture doc carries an altitude disclaimer saying
its decomposition is provisional; empty packages for undesigned work go stale and
imply progress that doesn't exist.

### Tier 6 is excluded from the default pytest run

The tier table requires eval to be a scheduled job that never blocks a merge,
while the documented test command is bare `pytest`. Without a marker and
`addopts` those two rules contradict each other. Tier 6 now runs only via
`pytest -m eval`.

### docs/ is excluded from ruff

`ruff format` rewrites Python inside the architecture doc's fenced blocks,
destroying comment alignment that carries meaning. Docs are excluded so the
formatter can't silently edit prose.

## Verification

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest              # 10 pass; tier 6 excluded
uv run pytest -m eval      # tier 6 only when asked for
python -c "import dss"     # src layout resolves
```

The boundary check must be proven able to fail — plant `import pydantic_ai` under
`core/` and confirm both ruff and the test reject it, then confirm the same import
is allowed under `orchestration/`.

## Follow-ups

- Policy schema, default pack, domain models, and loader — the next change, with
  its own spec covering the design reasoning.
- The build-backend choice, `requires-python = ">=3.13"`, and the ruff config may
  each warrant an ADR. No sibling repo has a `pyproject.toml`, so this repo seeds
  the shared config rather than inheriting it.
