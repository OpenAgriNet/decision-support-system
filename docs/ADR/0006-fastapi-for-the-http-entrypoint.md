# ADR-0006: FastAPI for the HTTP entrypoint

- **Status:** ACCEPTED
- **Date:** 2026-09-04
- **Deciders:** DSS implementation
- **Consulted:** —
- **Informed:** Experience-layer engineering leads

---

## 1. Context and Problem Statement

ADR-0002 fixes the boundary as REST over HTTP with Server-Sent Events for
streaming, and `DSS_ARCHITECTURE.md` §8.3 lists the concrete interface as
undecided. `entrypoint/` therefore had no framework, and `router.py` could not be
written without one.

What the entrypoint actually has to do is narrow:

- one `POST` route
- read the **raw** body, because the contract distinguishes `400` (not JSON) from
  `422` (JSON that violates the schema), and because the decompressed-size cap
  has to be checked after gunzip
- read headers: `Accept`, `Content-Type`, `Content-Encoding`, `traceparent`
- stream a response with per-frame flush
- a startup hook, so the composition root builds the runner once

Notably **not** required: request-body binding (the wire models validate
manually), a DI container (`entrypoint/composition.py` is the composition root by
design), authentication, or CORS — only this deployment's own channel services
reach this port (ADR-0002 §2.3).

## 2. Decision Drivers

1. **Team familiarity.** Reviewers pay for an unfamiliar framework on every
   change.
2. **The manual `400`/`422` split must survive.** It is a contract requirement,
   not a preference.
3. **An accurate OpenAPI 3.1 document**, which `api-contract.md` §Open lists as
   owed.
4. **SSE with per-frame flush**, no buffering middleware.
5. **Weight.** A dependency whose features are switched off is a liability.
6. **Reversibility.**

## 3. Considered Options

- **A — FastAPI**
- **B — Starlette**
- **C — Litestar**

## 4. Decision Outcome

**Chosen option: A — FastAPI.**

Starlette was the better fit on the technical merits alone, and the entrypoint
was first built on it. The switch to FastAPI was made deliberately on driver 1:
a teammate had already reached for `fastapi` + `uvicorn` for the `examples/` curl
harness on `origin/57-implementation-policy-and-moderation`, and familiarity
across the team outweighs a difference of five imports.

The technical objection to FastAPI was that its two headline features are unused
here — and one of them is worked around rather than adopted:

- **Request binding is declined.** The handler takes a raw `Request`, because
  driver 2 requires the bytes before anything validates them. FastAPI therefore
  runs no validation of its own, and its `422` handler never fires — `problem.py`
  owns every non-200 body.
- **Schema inference is impossible** as a consequence, so the route would have
  documented no request body at all.

The second is closed rather than accepted: the route declares its schema through
`openapi_extra`, generated from `schema.TurnRequest.model_json_schema()` and
`schema.TurnResponse.model_json_schema()`. The generated document therefore
describes the same models the endpoint validates against, so the two cannot
drift, and driver 3 is satisfied — 9 nested models, both response media types,
and all eight status codes.

Litestar was rejected: it offers nothing the other two do not, and fails driver 1
hardest.

### 4.1 Positive Consequences

- **Driver 3 is met properly.** `/openapi.json` and `/docs` describe the real
  contract, generated from the wire models rather than hand-maintained.
- **Familiar to reviewers**, which is the whole point of the choice.
- **The manual parsing survives intact** — the `400`/`422` split, the gzip cap,
  and `Accept` negotiation are all still the router's own code, so behaviour did
  not change with the framework. All 107 tests passed unchanged across the
  switch.
- `uvicorn` is the server; `create_app()` is a factory, so
  `uvicorn --factory dss.entrypoint.app:create_app` is the run command.

### 4.2 Negative Consequences

- **A dependency carried for one of its three features.** Binding and DI are
  unused; only routing plus OpenAPI generation are.
- **`openapi_extra` is hand-wired.** It is generated from the wire models, so it
  cannot describe stale *fields* — but if a route's status codes change, the
  `responses` block has to be updated by hand.
- **`/docs` and `/openapi.json` are exposed** by default. Harmless on an internal
  port, and useful to the channel teams, but it is surface that Starlette would
  not have had. Disable with `docs_url=None, openapi_url=None` if the posture
  changes.
- **FastAPI is anyio-native** and so satisfies ADR-0005, but it pins its own
  Starlette range, which is one more version to reconcile.

### 4.3 Reversibility

High. Every import used is re-exported by both frameworks
(`Request`, `Response`, `JSONResponse`, `StreamingResponse`), so reverting means
changing `app.py` back to `Starlette(routes=[...])`, returning a `Route` instead
of an `APIRouter`, and dropping `openapi_extra`. Roughly twenty lines, and no
test would change.

## 5. Follow-up Actions

- **[DSS CODE OWNERS]** Update `DSS_ARCHITECTURE.md` §8.3 — the interface is no
  longer undecided.
- **[DSS CODE OWNERS]** Publish `/openapi.json` as the contract's OpenAPI 3.1
  artifact, closing that open item in `api-contract.md`.
- **[DSS CODE OWNERS]** Decide whether `/docs` stays exposed in a deployed image.

## 6. Notes

- Reconciliation risk: ADR numbers 0003–0005 are taken on
  `origin/feat/79-provider-discovery`, which also carries a second file numbered
  0002. This ADR takes 0006 to avoid a third collision.
- The concurrency cap uses `anyio.Semaphore` per ADR-0005, not an asyncio
  primitive.
