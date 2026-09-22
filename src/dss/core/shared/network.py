"""Two network terms that are domain language, not wire detail.

The envelope types live in `adapters/beckn/`, because a request body is
protocol. These two are different: they name things `core/` reasons about and
passes between services, so they belong where the domain does.

Plain aliases, not wrapper objects. There is no type checker in CI — ruff is
the only enforced tool — so a wrapper would have to validate at runtime to
enforce anything, and neither of these has a shape worth validating: a
transaction id is whatever the caller sent, and the set of `@type`s is whatever
the schema packs publish. What an alias buys is the name, at every signature
that used to read `str`.
"""

from __future__ import annotations

# A resource's `@type`, as a prefixed CURIE: `openagrinet:MandiPrice`. An open
# set, fed by whichever schema packs a deployment mounts — so never an enum.
# Called `capability` at most call sites, which is the DSS's word for the same
# thing seen from the planning side.
NetworkSchemaType = str

# The id that ties one farmer's question to every network call made answering
# it. Minted at the HTTP edge when the caller sends none, then threaded through
# unchanged — the DSS never mints a second one mid-turn. It is also the turn's
# `traceId`, which is why `TurnContext` carries no separate field for it.
NetworkTransactionID = str
