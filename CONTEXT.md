# Glossary

**Turn** — one farmer question in, one answer out. Named by its `transactionId`.

**Turn message id** — the `messageId` the caller sends with a turn. Says which message in the conversation this turn answers.

**Network message id** — the `messageId` the DSS makes fresh for each call it sends to the network (discover, select, status, support). One turn has many. Lets a DSS log line be matched to the network's own log line for the same call.

**Sender** — the client app that sent the turn, named by `senderId`.

**Details form** — the form a farmer fills in first for a scheme that needs an OTP. Sending it asks the provider to text the OTP.

**Init form** — the form that asks for what the provider sent, such as the OTP. It belongs to the details form it follows.

**Waiting flow** — a details form the provider has seen, kept for a short time until its init form comes back.

**Challenge method** — what the provider sent the farmer to answer. Only `OTP` today.

**Form action** — a button beside a form's submit, such as Resend OTP. Pressing it sends no values.
