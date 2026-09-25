"""A network term that is domain language, not wire detail.

The envelope types live in `adapters/network/`, because a request body is
protocol. This one is different: `core/`, the ports and the adapters all pass
it around, so it lives where the domain does.

A plain alias, not a wrapper object. There is no type checker in CI — ruff is
the only enforced tool — so a wrapper would have to validate at runtime to
enforce anything, and a transaction id has no shape worth validating: it is
whatever the caller sent. What an alias buys is the name, at every signature
that used to read `str`.

`NetworkSchemaType` lives with provider discovery, its only user.
"""

from __future__ import annotations

# The id that ties one farmer's question to every network call made answering
# it. Minted at the HTTP edge when the caller sends none, then threaded through
# unchanged — the DSS never mints a second one mid-turn. It is also the turn's
# `traceId`, which is why `TurnContext` carries no separate field for it.
NetworkTransactionID = str
