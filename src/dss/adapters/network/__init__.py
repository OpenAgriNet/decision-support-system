"""The wire envelope, shared by the discovery and invocation adapters.

Here rather than in `core/`: this is protocol detail. The terms that *are*
domain language live in `core/`: `NetworkTransactionID` in
`core/shared/network.py`, `NetworkSchemaType` in `core/provider_discovery/`.
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
