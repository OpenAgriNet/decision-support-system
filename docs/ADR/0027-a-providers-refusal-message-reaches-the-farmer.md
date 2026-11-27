# ADR-0027: A Provider's Refusal Message Reaches the Farmer

- **Status:** ACCEPTED
- **Date:** 2026-11-27
- **Deciders:** DSS implementation

---

## 1. Context and Problem Statement

When a provider refuses a form, the farmer reads only the DSS's own line.
A wrong OTP comes back from PM-KISAN as a 400 that says why:
`{"message":{"status":"NACK","error":{"code":"BIZ_GENERIC_ERROR","message":"the OTP is wrong or has expired"}}}`.
The farmer reads "The provider did not accept that response." and cannot
tell what to fix (#209). Until now the DSS dropped provider text on
purpose: "the farmer is told in the DSS's own words".

## 2. Decision Drivers

1. The farmer should know what to fix, in words the provider chose.
2. Generic: any form, any provider, any call. Nothing about OTPs.
3. No new field for apps to learn.
4. The farmer's values stay out of the audit record and the traces.
5. Outages and refusals of our own credentials stay in the DSS's words.

## 3. Considered Options

1. **The provider's message replaces the text line.** Read from the NACK,
   shown above the form when the provider refused the farmer's request.
2. **A new field on the answer**, such as `error.providerMessage`, for
   the app to show.
3. **A table of DSS words per provider error code**, in config.

## 4. Decision Outcome

Chosen option: **1.**

- The invocation adapter reads `message.error.message`, or a top-level
  `error.message`, from a refusal: a 4xx other than 401, 403, 408 and 429.
  It never reads an outage (5xx, timeout) or a refusal of our credentials.
- It keeps the message on one line, masks the farmer's values with the
  call's tags, and drops it when it is blank, not text, or longer than
  200 characters.
- It reaches core on `Failure.provider_message`. Where a refusal is
  worded (init, the init response, status, Resend OTP, a one-turn
  filing), the provider's message replaces the configured line. The
  field hint stays "Check this value."
- Core uses the message only for a refusal cause, and never for a
  refusal of our credentials, whatever the adapter sends.
- The form closer records the answer with its tags and gives the farmer
  their values back. A turn in another language goes through the
  localizer, which sees tags, not values; a rendering that loses or
  changes a tag is dropped for the English text.
- No message, or a failure that is not a refusal: the configured line,
  as before.

Option 2 asks every app to change, for text the DSS can already place.
Option 3 needs a table per provider before any farmer benefits; it can
still come later for known codes, on top of this.

## 5. Consequences

- No wire change; `cause` still says what happened, so an app with its
  own text per cause loses nothing. Stays `/v1`.
- The farmer reads provider wording, which the DSS does not control.
- #209 asks for "no provider error text" when the provider cannot be
  reached. That still holds: only a refusal shows the provider's words.
- A one-turn filing reads "refused" from `retryable`, so a 404 or 422
  there still reads as `provider_unavailable` and shows the DSS's line.
  Fixing that classification is its own story.
- A refused status check ends the flow with no form, so the farmer reads
  the reason with nothing to retry on. A follow-up story hands the init
  form back.

### Accepted risks

- Every 4xx message is shown, so a 400 caused by our own bad request
  (a schema fault such as `SCH_SCHEMA_VALIDATION_FAILED`) shows its
  technical text to the farmer.
- A link in the provider's text is clickable in the web app.
- An OTP is never masked in replies, as a short code would match other
  numbers there, so a message that repeats it reaches the audit record
  and the Langfuse trace.
- A voice channel will read the provider's text aloud, links and codes
  included. Decide when voice lands.
- Nothing yet says which turns showed a provider's message, or why one
  was dropped; a span attribute can add that later.
- The message enters the localizer's prompt for a turn not in English.
  An English farmer reads it with no model in between, so a provider
  could already say anything to them.
