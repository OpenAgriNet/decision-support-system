# Glossary

**Turn** — one farmer question in, one answer out. Named by its `transactionId`.

**Turn message id** — the `messageId` the caller sends with a turn. Says which message in the conversation this turn answers.

**Network message id** — the `messageId` the DSS makes fresh for each call it sends to the network (discover, select, status, support). One turn has many. Lets a DSS log line be matched to the network's own log line for the same call.

**Sender** — the client app that sent the turn, named by `senderId`.
