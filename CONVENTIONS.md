# Conventions

Repo-wide conventions for naming, versioning, git workflow, logging, and linting. Referenced from `AGENTS.md`.

## Naming Conventions

### Python
| Artifact | Convention | Example |
|---|---|---|
| Module | `snake_case.py` | `dss_models.py` |
| Class | `PascalCase` noun | `UserTurn` |
| Function | `snake_case` verb | `stream_dss_turn` |
| Constant | `UPPER_SNAKE_CASE` | `DEFAULT_CHANNEL` |
| Type alias | `PascalCase` | `LanguageCode = str` |

### API & contract naming
- Field names: `snake_case` in Python and JSON — no transformation at the boundary.
- Language codes: BCP-47 only (`gu`, `hi`, `en`) — never full names (`gujarati`).
- Channel values: lowercase (`web`, `whatsapp`, `voice`).
- IDs: always `str`, never `int`.
- History roles: `"user"` or `"assistant"` only — never `"human"`, `"bot"`, `"farmer"`.
- API versioning: version at module level on breaking changes, never suffix field names.

### MCP tool naming
- Tool names: kebab-case verb-noun — this is what the LLM sees: `get-crop-advisory`, `check-cattle-health`.
- Tool description: one plain-English sentence stating what it returns and when to use it — the LLM routes on this.
  ```
  # Bad — LLM will not route correctly
  """Gets crop data."""

  # Good
  """Returns crop advisory for a given crop and region.
  Use when the farmer asks about sowing time, fertilizers, or yield improvement."""
  ```
- Input/output models: `{ToolName}Input`, `{ToolName}Output`.

### Configuration & primitives
- Config files: `kebab-case.yaml` — e.g. `amul-identity.yaml`, `crop-advisory.md`.
- Primitive IDs: kebab-case — the override key; an adopter file with the same ID wins.

### Environment variables
`UPPER_SNAKE_CASE`, grouped by prefix. Booleans: `true` / `false` only.
```
LLM_FALLBACK_ENABLED=true
HINDI_CHAT_ENABLED=false
MCP_TOOL_TIMEOUT_SECONDS=10
```

### Repository naming
kebab-case, specific to the capability. No `oan-`/`dpg-` prefix — decided against prefixing.
Examples: `decision-support-system`, `knowledge-ingestion-and-retrieval`.

## Versioning
`MAJOR.MINOR.PATCH` — single source of truth in `pyproject.toml`.

| Bump | When |
|---|---|
| MAJOR | Breaking contract change — `UserTurn` shape, MCP tool name, Beckn endpoint |
| MINOR | New backwards-compatible capability |
| PATCH | Bug fix, no contract change |

Pre-release: `1.0.0-alpha.1` → `1.0.0-beta.1` → `1.0.0-rc.1` → `1.0.0`.

Release tags: annotated tags only, on `main` only, immutable once pushed.
```bash
git tag -a v1.1.0 -m "feat(dss): UserTurn contract and stream_dss_turn"
git push origin v1.1.0
```

## Changelog
`CHANGELOG.md` at repo root. `[Unreleased]` section always present.
```markdown
## [Unreleased]

## [1.1.0] - 2026-08-18

### Added
- UserTurn normalized turn contract (#42)
- get-crop-advisory MCP tool (#55)

### Fixed
- Fall back to source_lang when target_lang is empty (#38)
```

## Git Workflow

### Branch naming
`{type}/{issue-no}-{short-description}`
```
feat/42-dss-user-turn-contract
fix/38-translation-empty-target-lang
```

### Commit messages
`<type>: <summary in imperative mood> [#<issue-no>]`

Issue number makes commits grep-able: `git log --grep="#42"`.
Scopes are optional — add one only when the repo grows large enough that filtering by area is genuinely useful. Don't define them speculatively.

| Type | When | Version impact |
|---|---|---|
| feat | New capability | MINOR |
| fix | Bug fix | PATCH |
| refactor | No behaviour change | None |
| chore | Tooling, deps | None |
| test | Tests only | None |
| docs | Docs only | None |

`BREAKING CHANGE:` in footer → MAJOR bump regardless of type.

```
feat: introduce UserTurn contract and stream_dss_turn [#42]
fix: fall back to source_lang when target_lang is empty [#38]
refactor: delegate v2 path to stream_dss_turn [#61]
```

### Pull requests
- Title: follows Conventional Commits (drives changelog).
- Body: always ends with `Closes #<issue-no>` — auto-closes issue on merge, enables cycle-time tracking.

```markdown
## What
Introduces the normalized UserTurn contract and stream_dss_turn entry point.

## Why
Any adopter can now call DSS without knowing about the underlying pipeline.

## Testing
Smoke tested on Gujarati and English with equivalent v1/v2 output.

Closes #42
```

Traceability chain: Issue #42 → Branch `feat/42-...` → Commits `[#42]` → PR "Closes #42" → Merged → Issue closed.

### Merge strategy
Rebase merge only. Each commit lands individually on `main` — individual commits are the source of truth for the changelog and `git log` traceability.
- Squash within a branch is fine for cleanup (typos, formatting).
- Never squash the entire PR on merge — individual commit history is lost.
- If a PR is squash-merged by accident: the PR title and `Closes #issue` still preserve traceability and changelog correctness; only intra-PR commit granularity is lost.

## Logging
Always include `request_id`. Never log raw PII.
```python
# Good
logger.info("request_id=%s moderation_category=%s", request_id, category)
logger.warning("request_id=%s pretranslation_failed=True falling_back=True", request_id)
```
Levels: `DEBUG` internal state · `INFO` turn lifecycle · `WARNING` recoverable failure · `ERROR` unrecoverable failure.

## Linting & Formatting
One tool across all repos: **ruff**. Replaces black, flake8, and isort — one tool, one config, consistent across every repo.
- pre-commit: `ruff` lint + format runs automatically on every commit (fast, < 1s).
- pre-push: full test suite runs before code leaves local.
- CI: linting must also pass in CI — local hooks can be bypassed with `--no-verify` so CI is the safety net.
- Same ruff config copied into every repo's `pyproject.toml` — enforced in CI so it cannot drift silently between repos.
