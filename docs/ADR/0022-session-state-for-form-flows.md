# ADR-0022: Session State for Form Flows

- **Status:** ACCEPTED
- **Date:** 2026-11-05
- **Deciders:** DSS implementation
- **Amends:** `docs/DSS_ARCHITECTURE.md` §1.2, "the DSS never owns session state"

---

## 1. Context and Problem Statement

Some provider actions take two steps. To file a PM-KISAN grievance, the DSS
first calls `init`. The provider sends the farmer an OTP. On the next turn the
farmer sends the OTP back, and only then can the DSS call `support` (#207).

So the DSS must remember, between two turns, which form is waiting for an OTP
and where `init` went. Until now it kept nothing between turns: §1.2 says it
never owns session state, and the status form keeps nothing between checks.

The Experience API does not hold this for us today, and asking every calling
app to carry a half-finished filing back and forth would put provider details
in their hands.

Two turns in one session must also not run over each other, for example when
a farmer taps "send" twice. That needs a lock per session, and a lock is easy
to get subtly wrong when we write it ourselves.

## 2. Decision Drivers

1. A two-step filing must work across two turns.
2. Personal data held between turns must be as little, and kept as short, as
   possible.
3. Two turns in one session must not read and write the state at once.
4. A turn whose stream is closed early must still save and unlock, and a turn
   that hangs must not block its session for good.
5. It must work with more than one DSS replica.
6. We should not write and maintain our own locking.

## 3. Considered Options

1. **The calling app carries the state** in each request. Rejected: every app
   must change, and it would hold provider details it has no use for.
2. **The provider carries it**, through the transaction id alone. Rejected:
   the DSS still has to know which form the OTP belongs to.
3. **The DSS holds the state in its own memory**, with a lock we write.
   Rejected: it breaks with a second replica, and the lock is ours to get
   right.
4. **The DSS holds the state in Redis**, with redis-py's lock, behind a port.
5. **Redis with Redlock**, the lock spread over several Redis servers.
   Rejected: it needs five separate Redis servers and a library outside
   redis-py. It covers one Redis dying mid-turn, whose worst case here is two
   turns briefly overlapping.

## 4. Decision Outcome

Chosen option: **4.**

- **A plain port, `KeyValueStore`** (`ports/key_value_store.py`): `get`,
  `set` with a time to live, and `lock` for one key. Text in, text out. It
  knows nothing about sessions.
- **Redis behind it**, `RedisKeyValueStore` (`adapters/key_value/redis.py`).
  Redis drops an entry once its time to live passes.
- **A `SessionStore` on top**, in `orchestration/`. It takes a
  `KeyValueStore`, builds the key, sets the time to live, and turns the
  session state to and from text. Only the orchestrator uses it; the core
  never sees either.
- **The key is the sender and the session**, `sender_id:session_id`, so two
  calling apps reusing a session id keep apart.
- **Short life.** An entry lives 180 seconds, about as long as an OTP. The
  number is ours until the provider docs give one.
- **The lock is redis-py's single-server lock.** Taking it writes a key only
  if no one holds it, with an expiry. Releasing it deletes the key only if it
  still holds our token. The lock key has its own prefix, so saving the state
  never overwrites it.
- **The lock has a lease and an acquire timeout, both required.** The lease,
  `lease_seconds`, is how long the lock lives: past it the lock frees itself
  and the next turn takes it, so a turn that hangs or is lost never blocks its
  session for good. A lease of 0 or less is refused, as redis-py would read 0
  as "never expires". A release after the lease ends leaves the next turn's
  lock alone. The acquire timeout, `acquire_timeout_seconds`, is how long a turn
  waits to get the lock: past it the turn gets `LockAcquireTimeoutError` and
  ends `unavailable`.
- **Any task may release the lock.** A closed screen can leave the turn to be
  finished by another task, which must still release it.
- **Only form turns use it.** A text turn takes no lock, so quick text
  messages never queue.
- **What it holds.** Only details forms the provider has seen, by form id,
  each waiting for an OTP or done. The OTP itself is never stored. At most one
  flow is open per session.
- **Tested against a real Redis.** The adapter's tests start a Redis
  container (testcontainers), so the lock is tested on the server, not on an
  emulation.

## 5. Consequences

- §1.2 no longer holds for form flows: the DSS keeps a farmer's form values
  for up to three minutes, in Redis.
- **Every deployment needs its own Redis.** Not Langfuse's: that one is tuned
  for Langfuse and saves to disk. Ours must have a password, TLS, and saving
  to disk turned off (no RDB, no AOF), so form values never land on disk.
- A Redis restart loses every open flow. The farmer is asked to start again.
- A failover to a replica that had not caught up can drop a lock. Two turns
  in one session may then overlap once, and the last save wins. Accepted:
  rare, and not harmful here.
- A turn that runs past `lease_seconds` keeps running, and the next turn
  may run beside it, with the same result. The limit must be longer than a
  form turn takes, so this only happens to a turn that is already stuck.
- A killed process skips the save, so an OTP already used could still look
  open until the entry expires.
- These tests need Docker or Podman. Without it they skip on a laptop and
  fail in CI. Testcontainers' cleanup container (Ryuk) is off, as it cannot
  start on Podman on a Mac; a killed test run can leave a Redis container
  behind.
- Open: what a turn does when Redis cannot be reached. For now it starts
  from an empty state. This is a business question, on the standup.
- The key-value store is built and tested. `SessionStore`, the Redis setting
  and the wiring come with the form path's use of it.
