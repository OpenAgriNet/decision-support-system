# ADR-0023: Log Fields as Attributes

- **Status:** ACCEPTED
- **Date:** 2026-11-06
- **Deciders:** DSS implementation
- **Amends:** ADR-0014, which sends `dss` logs over OTLP to ClickHouse

---

## 1. Context and Problem Statement

Support engineers read DSS logs in HyperDX, which reads ClickHouse. They need
to find every line for one farmer request, one calling app or one provider
(engineering-tracker#241).

Our log lines kept their facts as text: `request_id=… external=discovery`.
HyperDX can search that text, but cannot filter on it. Some facts were also
missing: the sender, the id of each network call, the network, and a line per
HTTP request or per model call.

The knowledge provider will ship its logs to the same store. One id must find
lines from both services.

## 2. Decision Drivers

1. Filter, not search: one transaction id, sender or provider in HyperDX.
2. Same field names across services.
3. No farmer words in exported lines.
4. Readable on a laptop, with no HyperDX.

## 3. Considered Options

1. **Keep `key=value` text.** No change. HyperDX can only search it.
2. **Fields as log attributes; the message stays short text.**
3. **Both:** attributes, and the same fields repeated in the text. Twice the
   size for no gain.

## 4. Decision Outcome

Chosen option: **2.**

- **Fields ride on the log record.** OpenTelemetry exports them as
  attributes, which HyperDX filters on.
- **Shared snake_case names:** `transaction_id`, `turn_message_id`,
  `session_id`, `sender_id`, `message_id`, `provider_id`, `network_id`.
  `message_id` is the network message id of one discover, select, status or
  support call. `turn_message_id` is the caller's. `request_id` is gone; it
  was the transaction id.
- **A turn's ids are added once,** by a logging filter on both handlers. A
  missing id shows as `missing`, so a filter on it still finds the line.
- **One line per HTTP request:** method, path, status, duration and the turn's
  ids, under OpenTelemetry's HTTP names. Never the body or the query string.
  The health probe (`/openapi.json`) is skipped unless it fails.
- **A line before and after every model call:** agent, model, then status,
  duration and tokens. Never the prompt or the answer.
- **Failures:** a caught failure the turn answers around is WARNING, with its
  type and no stack. A line that reports a failed call or stage is WARNING
  too. An uncaught failure is ERROR, with its stack.
- **Stderr** prints the fields after the message as `key=value`, so a local
  grep still works.

## 5. Consequences

- A query or dashboard that read `request_id=` from the text must use the
  `transaction_id` attribute instead.
- The knowledge provider must use the same names for a cross-service filter
  to work.
- An uncaught failure's stack and message are exported. They can quote a
  farmer. Accepted: these are rare and needed to fix the bug.
- pytest's `caplog` does not run our filter, so a test that needs the turn's
  ids on a record adds the filter itself.
