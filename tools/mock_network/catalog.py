"""What the DSS asked for, read out of a discover request.

The wanted `@type`s arrive inside a jsonpath predicate:

    $.catalogs[*].resources[*]
        ? (@.resourceAttributes."@type" == "openagrinet:MandiPrice")

A regex over the literals is enough. Evaluating the jsonpath would mean
implementing a query language to recover strings the DSS put there itself, and
the mock has no catalog to evaluate it against — it *is* the catalog.
"""

from __future__ import annotations

import re
from typing import Any

_TYPE_LITERAL = re.compile(r'"@type"\s*==\s*"([^"]+)"')


def requested_types(body: dict[str, Any]) -> tuple[str, ...]:
    """Every `@type` the request filters on, in the order it names them.

    All of them, not the first: the DSS ORs several into one predicate when an
    ask spans capabilities, and answering one would make the turn partially
    answered for no reason a log would explain.

    A body with no filter yields none, rather than raising. The mock is a dev
    tool — answering nothing reads as `no_match`, where a traceback here plus
    a provider defect at the DSS is two puzzles instead of one.
    """

    expression = (
        body.get("message", {}).get("intent", {}).get("filters", {}).get("expression")
    )
    if not isinstance(expression, str):
        return ()
    return tuple(_TYPE_LITERAL.findall(expression))
