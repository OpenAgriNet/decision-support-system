# ADR-0004: Experience-API envelope mapping and per-component model bindings

- **Status:** ACCEPTED
- **Date:** 2026-09-02
- **Deciders:** DSS code owners
- **Consulted:** Product Owner
- **Informed:** Adopter engineering teams

---

## 1. Context and Problem Statement

Two related contract questions land together.

**Inbound shape.** The Experience API sends a turn as an OpenAI-style thread, not a
flat query:

```jsonc
{
  "input": [ { "role": "user", "content": [ { "type": "text", "text": "…" } ] }, … ],
  "user_context": { "user_id", "reference_token", "issuer", "expires_at" },
  "attributes": { "sourceLanguage", "targetLanguage", "channel", "location", "response" }
}
```

The core works with a domain `UserTurn` (spec 0002 §5.1), which is deliberately
*not* this shape. Something must normalize the provider-shaped, camelCase envelope
into the domain object — and `UserTurn` must grow typed fields for the history,
location, and reference token the envelope now carries.

**Model binding.** The moderation slice hard-bound a single `moderation_model`.
With intent now a separate component (ADR-0003), each component needs its own
model, configurable per deployment, without touching code.

## 2. Decision Drivers

1. **Keep the core clean.** camelCase and provider JSON must not leak past the
   boundary; the core sees only domain types.
2. **Testable mapping.** The envelope→`UserTurn` transform is contract-critical and
   must be unit-testable without a running server.
3. **Config from the environment.** Model names are deployment config, not code —
   and per component, so intent and moderation can differ.
4. **Honour the contract's edge rules**, notably "an expired reference token counts
   as absent."

## 3. Considered Options

- **Option A — an `orchestration/envelope.py` boundary + flat per-component
  settings.** Pydantic request models mirror the envelope (camelCase aliases); a
  pure `to_user_turn(envelope, *, now)` maps to the domain object. `Settings` gains
  `intent_*` fields beside `moderation_*`, each env-bound via the existing `DSS_`
  prefix.
- **Option B — parse the envelope inline in the dev harness.** Map fields ad hoc in
  the FastAPI handler.
- **Option C — one shared model name for all components.** Keep a single
  `DSS_MODEL`.

## 4. Decision Outcome

**Chosen option: A.**

- **Boundary.** `orchestration/envelope.py` holds the `TurnEnvelope` request models
  and `to_user_turn`. Mapping rules:
  - the **last** `user` message is the current query (`original_query`, with
    `enriched_query` mirroring it until enrichment lands); everything before it
    becomes ordered `history`;
  - `attributes.sourceLanguage`/`targetLanguage`/`channel` → the turn's language and
    channel fields; `attributes.location` → typed `Location`;
    `attributes.response.max_characters` → `response_max_chars`;
  - `user_context.user_id` → `UserDetails.user_id`; `reference_token`/`issuer`/
    `expires_at` → `ReferenceToken`, **dropped when `expires_at <= now`**;
  - the envelope carries no session id, so `session_id` is keyed on `user_id`.
  - `content` accepts both the canonical list-of-parts and a bare string.
- **Domain types.** `core/shared/models.py` gains `ConversationMessage`, `Location`
  /`Geometry`, and `ReferenceToken`; `UserTurn.history` is typed
  `list[ConversationMessage]`.
- **Per-component models.** `Settings` carries `intent_model` and `moderation_model`
  (plus temperature/timeout/retries each), all read from `DSS_<COMPONENT>_*` env
  vars. The composition root builds one `PydanticAILLMProvider` per component.

### 4.1 Positive Consequences

- The core never sees camelCase or provider JSON; the transform is one pure,
  fully unit-tested function (tier 3).
- Deployments point intent and moderation at different models by setting
  `DSS_INTENT_MODEL` / `DSS_MODERATION_MODEL` — no code change.
- Contract edge rules (expired token, bare-string content, missing user turn) are
  enforced and locked by tests.

### 4.2 Negative Consequences

- `session_id` is derived from `user_id` because the envelope omits a session
  concept — a session key that is really a user key. Accepted until the envelope
  gains a session id (revisit trigger).
- The committed entrypoint is still undecided (DSS_ARCHITECTURE §8.3); the envelope
  is exercised only through the dev harness for now. The **mapping** is committed
  code; the **transport** is not.

## 5. Rejection Rationale

**Option B (inline in the harness)** buries a contract-critical transform in
throwaway code and makes it untestable without a server — it would have to be
rewritten the moment the real entrypoint is chosen.

**Option C (one shared model)** cannot express "intent on a small fast model,
moderation on a stronger one," which is the whole point of splitting the
components.

## 6. Revisit Triggers

- The Experience API adds a session identifier → stop keying `session_id` on
  `user_id`.
- The committed entrypoint (REST/gRPC/in-process) is chosen → move `TurnEnvelope`
  behind it and write its own ADR; `to_user_turn` should survive unchanged.
- A component needs more than a model name to bind (e.g. a base URL per component)
  → promote the flat settings to a nested per-component config block.

## 7. Follow-up Actions

- **[DSS code owners]** When the entrypoint ADR lands, re-home the transport but
  keep `to_user_turn` as the normalization seam.
- **[DSS code owners]** Reflected in `DSS_ARCHITECTURE.md` §3/§5 in this change.

## 8. Notes

- Pairs with ADR-0003: the envelope's `history` is exactly what moderation and
  intent consume for follow-up resolution.
- `orchestration/` is the correct home: it owns composition and is the one place
  (with `adapters/`) allowed to hold boundary/framework code.
