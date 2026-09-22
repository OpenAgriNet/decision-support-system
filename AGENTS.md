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
- HTTP framework: FastAPI on uvicorn (ADR-0006). One route, `POST /v1/turns`;
  `Accept` selects a single JSON body or the SSE stream. The composer always
  streams (ADR-0011) — SSE frames its pieces as `claim.delta`, JSON drains them.
- Async concurrency: anyio
- Dependency injection: Pydantic
- Package manager: uv
- Test framework: pytest
- Tracing: OpenTelemetry over OTLP, into self-hosted Langfuse (ADR-0007). Nothing
  imports `langfuse` — the endpoint is the seam, so another backend is a config change.

## Build & Run
Install: `uv sync`
Lint: `uv run ruff check .` · Format: `uv run ruff format .`
Test: `uv run pytest` — tier 6 (eval) is excluded; run it deliberately with `uv run pytest -m eval`
Run locally: `uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077` — `POST /v1/turns`, `Accept` selects JSON or SSE; on SSE the answer arrives as one `claim.delta` per piece as the composer writes it, then `claim.completed`. The full pipeline is wired (`orchestration/orchestrator.py` is the live runner: intent → moderation → discovery → planner → composer); discovery + invocation are gated on the three network settings, so without them a local turn is `no_match`. See [`docs/RUNNING.md`](./docs/RUNNING.md) for curl recipes, knobs, and what is still fake.

## Conventions
Naming, versioning, changelog, git workflow, logging, and linting conventions are documented separately in [`CONVENTIONS.md`](./CONVENTIONS.md) — read that file before naming anything, writing a commit, or opening a PR.

## Folder Structure

Hexagonal / ports-and-adapters. The hard rule: **only `orchestration/` imports the orchestration framework (Pydantic AI).** `core/` never imports it — core services take and return plain domain objects, so they stay unit-testable without a framework runtime and a future framework swap only rewrites `orchestration/` (and maybe some of `adapters/`), never `core/`.

```
src/dss/
├── core/                     # Framework-agnostic domain logic — plain Python in, plain Python out.
│                             # One subpackage per DSS logical function (see docs/DSS_ARCHITECTURE.md §3):
│                             # moderation, intent, enrichment, routing, persona, execution, review, channel,
│                             # stream_response (the composer's answer, yielded as it is written).
│                             # Create a subpackage when its slice is built, not ahead of it.
│                             # Never imports Pydantic AI, pydantic-graph, MCP, or any vendor SDK.
│   ├── <function>/           # Each logical function owns its types alongside its service:
│   │                         #   models.py   — the types this function produces/consumes
│   │                         #   service.py  — module-level functions, not use-case classes
│   │                         # Split models.py into a models/ package only when it earns it
│   │                         # (many types); a two-type models/ package is over-structured.
│   └── shared/               # Models and behaviour used across core services (UserTurn, UserDetails).
│                             # Cross-cutting only — a type used by one function lives with it.
│
├── ports/                    # Interfaces the core depends on (Protocols/ABCs) — no implementation here.
│                             # e.g. LLMProvider, ToolGateway, NetworkConsumerAdapter, CatalogCache, EvidenceSink.
│                             # This is the seam that lets core be tested with fakes and adapters be swapped.
│
├── adapters/                 # Concrete implementations of ports — the only place vendor/framework/network
│                             # code is allowed to live outside orchestration/. One subpackage per port family:
│                             # llm/ (vLLM, OpenAI, Azure, Anthropic clients), mcp/ (tool gateway), cache/, evidence/.
│
├── orchestration/            # The ONLY package that imports the orchestration framework (Pydantic AI today).
│                             # Wires core services as steps/agents, defines control flow and policy checkpoints
│                             # (moderation / pre-tool-call / post-response), owns the framework's state schema.
│
├── config/                   # Loads and validates the 5 configuration primitives (Identity, Skills, Policies,
│                             # Context Providers, Response Reviewers) mounted under /config per the extension model.
│
└── entrypoint/                # Whatever exposes the DSS to the Experience API — REST/gRPC/in-process is currently
                              # undecided (see docs/DSS_ARCHITECTURE.md); add an ADR when this is chosen.
```

## Testing Patterns
- Framework: pytest
- Style: TDD — write the failing test first before implementing.
- Test location: `tests/`, mirroring `src/dss/` (e.g. `tests/unit/core/routing/test_service.py`).

**Before writing any test, find your code's tier in this table and follow its row exactly:**

| Tier | Path | Code under test | Doubles / inputs | CI gate | Forbidden |
|---|---|---|---|---|---|
| 1. Core unit | `tests/unit/core/**` | `core/*/service.py` | Mock `ports/` Protocols; plain Python in/out | Every commit | Importing the orchestration framework; calling a real LLM/network |
| 2. Adapter contract | `tests/integration/adapters/**` | `adapters/**` | Recorded fixture or local test server | Every commit | Live network calls; asserting on `core/` business logic |
| 3. Orchestration integration | `tests/integration/orchestration/**` | `orchestration/**` (real Pydantic AI wiring) | Real graph/agent, mocked `ports/` below it | Every commit | Mocking the orchestration framework itself — that defeats the test's purpose |
| 4. LLM golden/replay | `tests/llm/golden/**` | Any LLM-in-the-loop flow | Recorded ("cassette") LLM responses, replayed | Every commit | Live LLM calls; asserting exact generated-text strings |
| 5. LLM structural | `tests/llm/structural/**` | LLM output shape | Real or replayed LLM call | Every commit | Asserting exact wording — assert schema/fields only (tool-call validates, `target_lang` honored, provenance present) |
| 6. LLM eval | `tests/eval/**` | End-to-end quality per domain | Live/small model, fixed query set, scored | Scheduled job only — **never** blocks a merge | Treating a score drop as a hard CI failure; running in the default `pytest` invocation |
| 7. E2E smoke | `tests/e2e/**` | Full `UserTurn → response` | Recorded/replayed LLM + tool responses | Every commit (keep this suite small) | Adding business-logic assertions here — that belongs in tier 1 |

**Hard rules:**
- One behavior → one tier. If a test needs two tiers' doubles at once, split it.
- Tier 1 must outnumber every other tier combined. If it doesn't, you're testing at the wrong layer.
- Never assert exact LLM-generated text anywhere except tier 4 (where the cassette makes it deterministic).
- Tier 6 (eval) is diagnostic, not a gate — wire it to a schedule/dashboard, not to merge checks.

## Known Gotchas
- `docs/DSS_ARCHITECTURE.md` §8 (Open items) is the authoritative list of what's still undecided (PII posture, envelope shape, primitive schemas, etc.) — check it before assuming a contract is final.
- Interface shape (REST/gRPC/in-process), database, and messaging choices are all undecided as of this writing — do not assume any of them; check `docs/DSS_ARCHITECTURE.md` and `docs/ADR/` first, and add an ADR when one is chosen.
