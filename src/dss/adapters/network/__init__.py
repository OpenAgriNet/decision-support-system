"""The wire envelope, shared by the discovery and invocation adapters.

Here rather than in `core/`: this is protocol detail. The two terms that *are*
domain language — `NetworkSchemaType` and `NetworkTransactionID` — live in
`core/shared/network.py` instead.
"""

from dss.adapters.network.models import (
    DiscoverContext,
    NetworkAction,
    NetworkContext,
    NetworkSchemaContext,
    NetworkVersion,
    SelectContext,
)

__all__ = [
    "DiscoverContext",
    "NetworkAction",
    "NetworkContext",
    "NetworkSchemaContext",
    "NetworkVersion",
    "SelectContext",
]
