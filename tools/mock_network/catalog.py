"""What the DSS asked for, read out of a discover request.

The wanted `@type`s arrive in the envelope's `schemaContext`, as the fragment
of each context URI:

    https://.../schema/MandiPrice/v0.1/context.jsonld#openagrinet:MandiPrice

They used to be read out of the jsonpath filter, which named them directly.
The filter now matches on `subjectCategories` instead ("Weather"), because
`schemaContext` is what pins a query to a resource type — so this reads the
types from the one place that still states them.

A regex over the fragment is enough. The mock has no catalog to evaluate a
jsonpath against — it *is* the catalog.
"""

from __future__ import annotations

import re
from typing import Any

# Everything after the first "#" in a context URI.
_CONTEXT_FRAGMENT = re.compile(r"#(.+)$")


def requested_types(body: dict[str, Any]) -> tuple[str, ...]:
    """Every `@type` the request names, in the order `schemaContext` lists them.

    All of them, not the first: the DSS names several when an ask spans
    capabilities, and answering one would make the turn partially answered for
    no reason a log would explain.

    A body with no `schemaContext` yields none, rather than raising. The mock is
    a dev tool — answering nothing reads as `no_match`, where a traceback here
    plus a provider defect at the DSS is two puzzles instead of one.
    """

    contexts = body.get("context", {}).get("schemaContext")
    if not isinstance(contexts, list):
        return ()
    found = []
    for context in contexts:
        if not isinstance(context, str):
            continue
        match = _CONTEXT_FRAGMENT.search(context)
        if match is not None:
            found.append(match.group(1))
    return tuple(found)
