# Plan: senderId, receiverId and networkId on every network call

## Context
Today `discover` sends no participant ids. `select`, `support` and `status` send
a fixed `senderId` and `receiverId` from settings (`"oan"` as receiver), and no
`networkId`. The network now needs these ids on every call, as finalised:

| | Discover | Select / Support / Status |
|---|---|---|
| senderId | DSS sender (see toggle) | DSS sender (see toggle) |
| receiverId | `network_host_id` | the capability's `provider_id` (the adapter swaps it for the provider URL) |
| networkId | `network_host_id` (required) | not sent |

**Sender toggle.** `network_sender_from_turn` (default off).
- Off: `senderId` is `network_sender_id` (the DSS's own id).
- On: the turn's `senderId`; when it is missing or blank, `network_sender_id`.

No ADR (agreed). Work in a new worktree off `origin/main`; copy this plan to
`docs/.agent/plan/<issue>-network-participant-ids.md` on that branch.

## Approach

**1. Settings** — `src/dss/config/settings.py`
- Add `network_host_id: str | None = None`. When `network_enabled` and it is
  unset, boot fails (same place the other network guard lives).
- Remove `network_receiver_id` (select's receiver is now the provider).
- Add `network_sender_from_turn: bool = False`. Keep `network_sender_id`.

**2. Choose the sender per turn, in orchestration** — `orchestration/discovery.py`
- `NetworkSenderFor = Callable[[UserTurn], str]`. `composition.py` picks one
  of two at wiring time from the toggle: on → `turn.sender_id` or the
  configured id when it is `None`; off → always the configured id.
- `UserTurn` is unchanged: a required field would touch 37 call sites. The
  mapping already turns a blank `senderId` into `None` (`mapping.py:76`).
- `UserTurn.sender_id` (the caller, used by provider preferences) is unchanged.

**3. Envelope** — `adapters/network/models.py`
- `DiscoverContext` gains `sender_id`, `receiver_id`, `network_id`.
- `SelectContext`, `SupportContext`, `StatusContext` keep `sender_id` and
  `receiver_id`; no `network_id`.
- Remove the docstring rule "a discover names no participants".

**4. Ports** — `ports/discovery.py`, `ports/invocation.py`
- `discover`, `select`, `support`, `status` each gain keyword `sender_id: str`.

**5. Discover adapter** — `adapters/discovery/client.py`
- `HttpCapabilityDiscovery` takes `network_host_id` at construction (wired in
  `orchestration/discovery.py:build_capability_discovery` and
  `entrypoint/composition.py`).
- `build_discover_request` takes `sender_id` and `network_host_id`; the host
  is both receiver and network, so one argument keeps them from drifting.

**6. Invocation adapter** — `adapters/invocation/client.py`
- Drop `sender_id`/`receiver_id` from `HttpCapabilityInvocation.__init__`
  (and `composition.py:636`).
- `build_select_request`, `build_support_request`, `build_status_request`:
  `receiver_id = capability.provider_id`, `sender_id` from the call.
  Support/status already receive `capability`.

**7. Callers pass the sender**
- `core/provider_discovery/service.py`: `discover_providers` takes
  `sender_id` and passes it to `discovery.discover`. `build_discover_providers`
  supplies it from `network_sender_for(turn)`.
- `orchestration/planner.py`: `PlannerDeps` gains `network_sender_id`, filled
  by orchestration from the same `NetworkSenderFor`. `select` and `_send_form`
  (support and status) pass it.

**8. Docs**
- `docs/DSS_ARCHITECTURE.md`: envelope ids table above. Record that the toggle
  forwards the caller's `senderId` unchecked: off by default, and safe only
  because the DSS is reachable from the Experience API alone. This reverses the
  spec 192 rule that select must not forward the caller's id.
- `docs/PLAYBOOK.md`: the three settings (`DSS_NETWORK_HOST_ID`,
  `DSS_NETWORK_SENDER_FROM_TURN`, `DSS_NETWORK_SENDER_ID`).
- Test fakes of the ports gain the `sender_id` argument.

## Tests (strict TDD, one test at a time)
1. Tier 2 — discover request has `senderId`, `receiverId`, `networkId`.
2. Tier 2 — select request: `receiverId` is the provider id; no `networkId`.
3. Tier 2 — support request, same.
4. Tier 2 — status request, same.
5. Tier 2 — adapters put the passed `sender_id` on the wire.
6. Unit (orchestration) — toggle off: configured id, even when the caller sent one.
7. Unit (orchestration) — toggle on, caller sent one: caller's id.
8. Unit (orchestration) — toggle on, no caller id: configured id.
9. Tier 1 — discovery service passes the given `sender_id` to the port.
10. Tier 3 — planner passes it to `select`, `support` and `status`.
11. Unit (settings) — network on without `network_host_id`: boot fails.

## Verification
- `uv run pytest` and `uv run ruff check .`
- Run locally with the network settings, `POST /v1/turns` with and without
  `context.senderId`, toggle on and off; check the outbound bodies in the
  DEBUG log or the trace.

## Open notes
- `tests/support/participants.py` (untracked) builds a `NetworkParticipants`
  type that does not exist. This plan does not need it. Leave it to you.
