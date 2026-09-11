# decision-support-system

[![codecov](https://codecov.io/gh/OpenAgriNet/decision-support-system/branch/main/graph/badge.svg?token=5D3QJ4STZM)](https://codecov.io/gh/OpenAgriNet/decision-support-system)
[![Security](https://github.com/OpenAgriNet/decision-support-system/actions/workflows/security.yml/badge.svg?branch=main)](https://github.com/OpenAgriNet/decision-support-system/actions/workflows/security.yml)

A reasoning runtime that interprets user queries and delivers curated answers from across network provider sources.

## Run it

```bash
uv sync
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

`POST /v1/turns` — the `Accept` header selects `application/json` or
`text/event-stream`. The reasoning is stubbed today; the transport, the port,
and the turn flow are real.

See [`docs/RUNNING.md`](./docs/RUNNING.md) for curl recipes, the error paths,
the runtime knobs, and which parts are still fake. Two pieces of configuration
are worth knowing before a first run: the **schema packs**, which nothing
clones, and the **district index** (`src/dss/config/districts.csv`), which the
`/discover` spatial filter needs and without which the app refuses to boot.

## Docs

- [`CLAUDE.md`](./CLAUDE.md) — layout, tech stack, testing tiers
- [`CONVENTIONS.md`](./CONVENTIONS.md) — naming, commits, git workflow
- [`docs/DSS_ARCHITECTURE.md`](./docs/DSS_ARCHITECTURE.md) — boundary and open items
- [`docs/HEXAGONAL_ARCHITECTURE.md`](./docs/HEXAGONAL_ARCHITECTURE.md) — ports and adapters
- [`docs/ADR/`](./docs/ADR) — accepted decisions
