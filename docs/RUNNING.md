# Running the DSS locally

`POST /v1/turns` works end to end today. **The reasoning is stubbed** — moderation
decides from a word list, intent classification returns a canned answer, and the
composer returns fixed English sentences whatever you ask. Every seam around them
is real: the wire contract, the port, the runner's ordering, the evidence sinks.

So this is for exercising the *transport and the flow*, not the answers.

## Start it

```bash
uv sync
uv run uvicorn --factory dss.entrypoint.app:create_app --port 8077
```

`--factory` because `create_app()` builds the app rather than being one. Add
`--reload` while you are poking at the code. `Ctrl-C` stops it.

Two pages come for free once it is up:

| URL | What |
|---|---|
| <http://127.0.0.1:8077/docs> | Swagger UI — the request/response schemas, generated from the wire models |
| <http://127.0.0.1:8077/openapi.json> | the OpenAPI 3.1 document |

The endpoint parses the body itself (so `400` and `422` can be told apart), which
means FastAPI cannot infer the schema — it is declared explicitly in
`router.py::_OPENAPI` from `TurnRequest.model_json_schema()`. Same models the
endpoint validates against, so the docs cannot drift from the code.

## Where the evidence goes

Every turn writes two files under `var/evidence/` (gitignored). One record per
line, appended — so `tail -f`, `grep` and `jq` all work.

```bash
tail -f var/evidence/telemetry.jsonl | jq -c '{trace_id, stage, outcome}'
tail -f var/evidence/turns.jsonl     | jq -c '{trace_id, event}'
```

**The two files are two different tiers** (`api-contract.md` §6), and the
difference is the point:

| File | Holds | Access | If a write fails |
|---|---|---|---|
| `telemetry.jsonl` | stage, outcome, join keys. **No user content** | broad, long retention | swallowed — the turn still answers |
| `turns.jsonl` | the question, the answer, the refusals. Farmer content **on purpose** | restricted, short retention | **the turn fails** — a lost audit record is not a success |

An answered turn and a rejected one, from one run:

```
telemetry.jsonl
  2a58d9c6  moderation  proceed
  2a58d9c6  intent      mandi-prices
  2a58d9c6  channel     2
  6ddbffda  moderation  reject          <- one line, and nothing after it

turns.jsonl
  2a58d9c6  opened  query='इस सप्ताह आनंद मंडी में गेहूं का भाव क्या है'
  2a58d9c6  closed  answered  content=2 sources=1
  6ddbffda  opened  query='sell fertiliser illegally'
  6ddbffda  closed  rejected  content=1 sources=0
```

**Read the shape, not just the presence.** The rejected turn emits one telemetry
line — no `intent`, no `channel` — so the gate holding is visible in the file.
The `trace_id` joins the two files for one turn.

**`geometry` is dropped on write.** `region` and `area` are kept. A stable user
id beside a precise point, over many turns, is a home address
(`api-contract.md` §6).

### The external endpoint

Evidence is meant to go to an external API, not to disk. That endpoint does not
exist yet, so the destination is a setting and the files are the stand-in:

```bash
DSS_EVIDENCE_DIR=/tmp/dss-evidence uv run uvicorn --factory dss.entrypoint.app:create_app
DSS_EVIDENCE_URL=https://evidence.internal/v1/events uv run uvicorn --factory ...
```

`DSS_EVIDENCE_URL` is **declared but not wired**, and setting it warns at startup
rather than being silently ignored:

```
UserWarning: DSS_EVIDENCE_URL is set but posting evidence to an external
endpoint is not implemented — records are being written to var/evidence instead.
```

Two things have to be settled before it can be wired: which HTTP client (this
repo has none as a runtime dependency; the provider-discovery branch uses
`httpx2`), and **what an unreachable endpoint should do to a turn.** The turn
sink is required, so on today's rules a failed write fails the turn — which would
make every turn depend on the evidence API being up. That is a real decision, not
a detail.

## The wire is camelCase## The wire is camelCase

`sessionId`, `transactionId`, `sourceLanguage`, `maxCharacters`, `traceId`,
`sequenceNumber` — per `docs/api-contracts/openapi.yaml`. Python stays
snake_case; aliases on the wire models bridge the two, and nothing inward of
`mapping.py` ever sees a camelCase name.

Input is strict. A snake_case key is an unknown field, and the contract sets
`additionalProperties: false`, so it comes back `422`. `context.transactionId` is
required — it is the correlation key, echoed back as `context.traceId`.

Responses are validated against `docs/api-contracts/openapi.yaml` in CI by
`tests/conformance/v1/test_against_openapi.py`, so what the server sends and what
the spec promises cannot drift.

## Send a turn

A ready-made body is at `docs/api-contracts/examples/answered_streaming.json`.

### Streaming

```bash
curl -N -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'traceparent: 00-9f2b7c1a487a9138e394d31b51134a61-00f067aa0ba902b7-01' \
  --data-binary @docs/api-contracts/examples/answered_streaming.json
```

**`-N` matters.** Without it curl buffers and the frames arrive in one lump, which
hides the thing you are trying to look at.

Expect four frames:

```
event: turn.created      sequenceNumber 1
event: claim.completed   sequenceNumber 2
event: claim.completed   sequenceNumber 3
event: turn.completed    sequenceNumber 4   outcome.status "answered"
```

The contract sets `sequenceNumber` minimum 1, so the stream is 1-based.

The `traceId` in every frame body is the one from your `traceparent`. Omit that
header and the DSS mints one — a turn always has an evidence key.

### Non-streaming

```bash
curl -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  --data-binary @docs/api-contracts/examples/answered_streaming.json \
  | python3 -m json.tool
```

Same turn, one body, no event wrapper, and no `sequence_number`. Dropping the
`Accept` header gives you this too — JSON is the default. **The endpoint does not
take a streaming flag; `Accept` decides.**

### The moderation gate

Put any of `illegal`, `illegally`, `gold loan`, `weapon` in the question. This is
the one path that runs real core logic:

```bash
curl -X POST http://127.0.0.1:8077/v1/turns \
  -H 'Content-Type: application/json' \
  -d '{"context":{"id":"api.dss.turn","envelopeVersion":"1.0.0",
        "timestamp":"2026-09-04T08:00:00Z","sessionId":"conv_1","transactionId":"txn_1"},
       "message":{"input":[{"role":"user","content":[
         {"type":"text","text":"how to sell fertiliser illegally"}]}],
        "attributes":{"sourceLanguage":"en","targetLanguage":"en","channel":"web"}}}'
```

```json
{"outcome": {"status": "rejected", "cause": "unsafe_illegal"},
 "content": [{"type": "refusal", "text": "I can only answer agriculture and livestock related questions."}],
 "sources": []}
```

Note what it is **not**: still HTTP `200`, and zero claim events before the
terminal one. A refusal is a turn the DSS completed, not an error.

## Poking the edges

Every one of these is a test case as well as a curl.

| Try | Get |
|---|---|
| `--data-binary '{not json'` | `400 malformed_request` |
| any extra field, e.g. `attributes.temperature` | `422 extra_forbidden` |
| a snake_case key, e.g. `sessionId` sent as `session_id` | `422` — the wire is camelCase |
| `"input": []` | `422 too_short` |
| `-H 'Content-Type: text/plain'` | `415` |
| `-H 'Accept: application/xml'` | `406` |
| `-X GET` | `405` |

A dependency failure is **never** a 5xx — it comes back as `200` with
`outcome.status: "unavailable"`. Once the first response byte is written the
status code cannot change, so every later failure is a terminal event instead.

## Knobs

`src/dss/entrypoint/settings.py`:

| Setting | Default | Set it to see |
|---|---|---|
| `max_concurrent_turns` | `32` | `0` → every turn `429` with `Retry-After` |
| `max_body_bytes` | `1000000` | something small → `413` (checked *after* gunzip, so a small gzip can still trip it) |
| `ready` | `True` | `False` → `503` |
| `dss_release` | `"v1.0.0"` | anything — it is echoed as `context.dss_release` |

## What is fake, and where to swap it

Every stub is marked `STUB(#nn)` in the source. Grep for it.

| Stub | File | Real version |
|---|---|---|
| the canned answer | `adapters/llm/stub.py` | a real `LLM` adapter |
| the deny word list | `core/moderation/service.py` | #82 policy evaluator |
| the fixed prompt | `core/intent/service.py` | #83 real prompt |
| the fixed sentences | `core/channel/service.py` | #84 composer + reviewer |
| the in-memory turn record | `adapters/sinks/memory.py` | #85 durable store |

Swapping any of them is a one-line change in
`src/dss/entrypoint/composition.py` — the only file that names concrete classes.

## Tests

```bash
uv run pytest                 # everything; tier 6 (eval) excluded
uv run pytest tests/unit      # pure — mapping, framing, core rules, boundaries
uv run pytest tests/integration/entrypoint     # the HTTP layer against a fake runner
uv run pytest tests/integration/orchestration  # the runner against fake ports
uv run ruff check . && uv run ruff format --check .
```

## Related

- `docs/api-contracts/api-contract.md` — the contract (still describes the older
  three-endpoint shape; being rewritten)
- `docs/.agent/plan/0002-v1/turn_API_contract.md` — the build plan and the
  reconciled contract rules
- `docs/HEXAGONAL_ARCHITECTURE.md` — why the layers are shaped this way
