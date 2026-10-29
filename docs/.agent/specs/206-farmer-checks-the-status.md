# Spec: Farmer checks a grievance's status with the form (#206)

Story: https://github.com/OpenAgriNet/engineering-tracker/issues/206
Builds on: `docs/.agent/specs/205-farmer-submits-the-form.md`. The offer of the
status form is on `main` already (an `observe` grievance ask gets the PMFBY
`status` form). This spec is the submission: the DSS reads the case and tells
the farmer where it stands.

## Approach

**Same path as filing.** A submitted status form runs on `FormPath`, as a
filing does: check the values, moderate the free text, one ask, discovery, the
scheme filter, the planner. Nothing in the path changes for status except the
tool the planner is given and the words used when it fails. The PMFBY status
form has no free text, so moderation passes it without a model call.

**A `status` tool beside `support`.** The 205 spec planned "a tool per pack
action, `support` now, `status` with #206". The planner gets a `status` tool
with the same guards as `support`: the turn cleared moderation, one call per
ask, and the model only picks the desk. The body is built in code from the
pack's `ActionSchema`, the desk and the form, by the same
`build_action_attributes`. The skill lists both tools; the ask line says
"checks the status of the farmer's form" instead of "files the farmer's form".

**Where the body goes.** The pack's `x-beckn-container-by-action` names
`commitmentAttributes` for `status`. The request is
`message.contract.commitments[0].commitmentAttributes`, with a fresh
`contract.id` and the one thin `Resource` the README asks for. The reply is read
from the same place. Endpoint `/status`, by Beckn convention, as `/support` is.

**A fresh session per check.** Each check uses the turn's own
`transactionId`, and the DSS keeps nothing from the filing. The farmer can ask
as often as they like.

**What comes back.** The case's `comesBack` fields, less the ones the farmer
typed (`applicantMobile`, `case.ticketNo`), become a `Result` on the ask, as a
filing's reply does. The composer writes the answer from it: the status, the
filing date, the crop and the complaint text.

## Decisions the pack leaves open

1. **No complaint found.** The pack does not say how a desk reports a ticket
   it does not know. This slice reads a refused status call (400, 401 or
   403, the codes `classify_status_code` treats as a defect) as "no complaint
   found for that ticket and phone": `unavailable`, cause `provider_rejected`,
   `retryable` false. A desk that cannot be reached (timeout, 5xx, and any
   other code) is "the status could not be checked right now": `unavailable`,
   cause `provider_unavailable`. Both keep the form with a `form_error` item
   with no fields, the contract's shape for "the values were fine, the
   provider could not take them". `requires_input` was considered and
   dropped: the contract keeps it for a `form_error` that names fields.
2. **Status wording.** The pack sends only a code (`Registered`,
   `UnderReview`, `Resolved`, …) and refuses the portal's phrase. The composer
   says the code in plain words ("under review"). No fixed label table yet.
3. **The ticket number is not repeated.** It is a value the farmer typed, so
   it is stripped from the reply like the phone. The farmer already has it.

## Changes

- **Port and adapter.** `CapabilityInvocation.status(capability, attributes,
  transaction_id, *, visibility) -> ActionReply`, `StatusFailedError`, retried
  like `support`. `build_status_request` and `map_status_response` in the
  invocation client. `NetworkAction.STATUS`. `_UnwiredInvocation` and the test
  fakes follow.
- **Planner.** A `status` tool, registered beside `support`. The skill and the
  ask line name it.
- **Form path.** A failed check gets status words, not filing words: two new
  `clarification-text.yaml` messages, `status_not_found` and
  `status_not_checked`. The turn's status and cause are a filing's.
- **Composer prompt.** A status answer gives the status in plain words, the
  filing date, the crop and the complaint text, never the phone or the ticket.
- **Mock network.** `POST /status` answers the case `/support` filed, for the
  same ticket and phone, and refuses anything else with a 400, so the
  not-found path can be run end to end.

## Tests

- Tier 1: the status request body against the pack's read-case example.
- Tier 2: the `/status` adapter: request, reply, retries, a 4xx and a 5xx.
- Tier 3: the planner's `status` tool through a `FunctionModel`; the form path
  ends `answered` on a case, `unavailable` with the not-found words on a
  refused call, and with the could-not-check words on a desk that is down.
- Tier 7: "what happened to my PMFBY complaint" to the status form, then a
  submission to an answer, over HTTP against the mock network.

## Out of scope

- PM-KISAN: its status needs an OTP step the DSS cannot run yet.
- Finding a ticket for a farmer who lost it. PMFBY answers per ticket only.
- Masking the ticket number before a model sees it (open in #206).
