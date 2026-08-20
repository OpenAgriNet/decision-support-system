# decision-support-system

## Service Overview
A reasoning runtime that interprets user queries and delivers curated answers from across network provider sources. This repo implements the **Decision Support System (DSS)** — an Experience Layer module of the OpenAgriNet (OAN) DPG, covering the Agriculture and Livestock domain (fishery may extend the taxonomy later).

## Architecture & domain reference
Do not duplicate architecture, domain model, or design-decision detail here — it goes stale immediately. The authoritative sources are:
- **`docs/DSS_ARCHITECTURE.md`** — DSS boundary, responsibilities, interfaces, logical functions, extension model, domain language, PII posture, open items. Read this before making any design decision.
- **`docs/ADR/`** — accepted architecture decisions.

**Keep the architecture doc current.** Whenever a design decision is made (framework choice, API shape, database/messaging choice, PII handling, etc.), update `docs/DSS_ARCHITECTURE.md` in the same change — do not let code and doc drift apart.

**Write an ADR for new tech-stack or design-direction decisions.** Any choice with real trade-offs — a new framework, library, database, messaging system, architectural pattern, or reversal of an existing ADR — gets a new `docs/ADR/NNNN-title.md` following the existing ADR format (context, decision drivers, considered options, decision outcome, consequences). Don't make load-bearing decisions silently in code or PR descriptions.

**Update this Tech Stack section whenever an ADR is accepted.** The list below is the current stack — kept current so this file can be trusted without re-reading every ADR. Whenever an ADR status flips to Accepted, update the entry here (and remove/replace what it supersedes) in the same change.

## Tech Stack
- Language: Python
- Orchestration framework: Pydantic AI
- Dependency injection: Pydantic
- Package manager: uv
- Test framework: pytest

## Build & Run
<!-- fill in — no build/run commands committed yet; this is a greenfield repo -->
Build: `# fill in — build command not detected`
Test: `pytest`
Run locally: `# fill in — run command not detected`

## Conventions
Naming, versioning, changelog, git workflow, logging, and linting conventions are documented separately in [`CONVENTIONS.md`](./CONVENTIONS.md) — read that file before naming anything, writing a commit, or opening a PR.

## Testing Patterns
- Framework: pytest
- Style: TDD — write the failing test first before implementing.
- Test location: `# fill in — not yet established, e.g. tests/**/test_*.py`

## Known Gotchas
- `docs/DSS_ARCHITECTURE.md` §8 (Open items) is the authoritative list of what's still undecided (PII posture, envelope shape, primitive schemas, etc.) — check it before assuming a contract is final.
- Interface shape (REST/gRPC/in-process), database, and messaging choices are all undecided as of this writing — do not assume any of them; check `docs/DSS_ARCHITECTURE.md` and `docs/ADR/` first, and add an ADR when one is chosen.
