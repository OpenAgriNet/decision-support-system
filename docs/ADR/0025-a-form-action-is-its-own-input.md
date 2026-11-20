# ADR-0025: A Form Action Is Its Own Input

- **Status:** ACCEPTED
- **Date:** 2026-11-20
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

A PM-KISAN OTP can be late or lost. The farmer should get a new one
without filling in the details again (#209). The OTP form needs a
Resend OTP button, and the app needs a way to say it was pressed.

## 2. Decision Drivers

1. The app keeps no personal data to resend.
2. No model needed to understand a press.
3. An older DSS must refuse a press, not misread it.
4. The same shape works on web, WhatsApp and voice.
5. Room for a cap and a cooldown later, without breaking apps.

## 3. Considered Options

1. **A new input item, `form_action`.** The form lists its buttons in
   `actions`; a press sends `{formId, context, name, actionId}`.
2. **A flag on the form item** (`command: resend_init`).
3. **Send the details form again.**

Others do both. Ory Kratos and Adaptive Cards put a flag on the same
submit; Slack, WhatsApp and AWS Cognito send a separate message. The
split follows one question: does the action carry the form's values?
Resend carries none.

## 4. Decision Outcome

Chosen option: **1.**

- A form may carry `actions`: a `name` and a `label` each. Known today:
  `resend_init`. An app skips a name it does not know.
- A press is its own input, with its own path. It calls `init` again
  with the waiting details, at the same provider. No moderation, no
  discovery, no composer.
- Which buttons an init form offers depends on the challenge method,
  not the scheme. Only `OTP` offers Resend. It is a table in code.
- Sent: the same form comes back. Not sent: the waiting entry is kept,
  because the old OTP may still work.
- A button the waiting form never offered sends nothing, and is answered
  as if nothing were waiting.
- A pack that gives no init answers a press text only, as for the details
  form.

Option 2 fails open. An older DSS ignores the flag and reads the press
as an OTP submission. It also bends "a form item is a submission".
Option 3 makes the app keep the enrolment number, runs moderation and
discovery again, and may pick another provider.

## 5. Consequences

- A new kind of input, so an older DSS answers a press with 422.
- Each resend restarts the waiting entry's 180 seconds (ADR-0022), so
  the details are held longer.
- No cap, no cooldown, no expiry shown yet. A double tap sends two
  SMS. A later story adds `availableAfterSeconds` and `remaining` to an
  action, and causes for too soon and too many. None of it breaks apps.
- A form handed back with errors (`form_error`) lists no buttons. The
  app keeps those of the form it is showing.
- Form labels and button text now go through the localizer, one short
  model call each on a turn that is not in English, on every form, not
  only the OTP form. Option labels and `form_error` labels stay English.

### Accepted deviations from the plan

- No `supports_resend` on the challenge method. A table of buttons by
  method does the same job, and adds a method without a branch.
- The first PR did not move the shared form-turn steps out of
  `form_path.py`. That move is this change's first commit, where the
  new path needed it.
