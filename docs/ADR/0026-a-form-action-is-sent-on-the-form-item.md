# ADR-0025: A Form Action Is Sent on the Form Item

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
3. Submit is a button too: one way to send a form back, whichever
   button was pressed.
4. The same shape works on web, WhatsApp and voice.
5. Room for a cap and a cooldown later, without breaking apps.

## 3. Considered Options

1. **A new input item, `form_action`.** The form lists its buttons in
   `actions`; a press sends `{formId, context, name, actionId}`.
2. **The button on the form item.** The form is sent back with
   `action: resend_init` and no values; no `action` means submit.
3. **Send the details form again.**

Others do both. Ory Kratos and Adaptive Cards put a flag on the same
submit; Slack, WhatsApp and AWS Cognito send a separate message. The
split follows one question: does the action carry the form's values?
Resend carries none.

## 4. Decision Outcome

Chosen option: **2.** Option 1 was built first, then changed in
review, before release.

- A form may carry `actions`: a `name` and a `label` each. Known today:
  `resend_init`. An app skips a name it does not know.
- A press is the `form` item with `action` set to the button's `name`,
  a new `submissionId`, and no field values. No `action`, or `submit`,
  is the submission.
- The wire has one item; the DSS does not. The mapper makes a press its
  own input, `FormAction`, with its own path. It calls `init` again
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

Option 1 gave the app two ways to send one form back, though a press is
a form sent with another button. Its gain, an older DSS refusing a
press, does not apply: no DSS without the button was ever sent one.
Option 3 makes the app keep the enrolment number, runs moderation and
discovery again, and may pick another provider.

## 5. Consequences

- `form` gains an optional `action`; the change is additive and stays
  `/v1`. The schema cannot say "no values when `action` is set", so the
  mapper refuses values on a press.
- An older DSS ignores `action` and reads a press as a submission with
  no OTP. It hands the form back as missing a value; nothing is filed.
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
- experience-api has no single API test for the whole press path (press
  in, `form_action` to the DSS, `actions` out). Each step has its own
  unit test, and one API test shows a press reaches the DSS.
