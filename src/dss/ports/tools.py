"""The tool-index seam (design v2 §5, §6.5).

The MCP client and the keyword (BM25) index live in an adapter; ``core/`` sees
only this port and the ``Tool`` value type. ``search`` narrows a long tool list
to the terms of one ask; ``refresh`` reconciles after a ``tools/list_changed``
notification.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dss.core.tool_discovery.models import Tool


class ToolIndex(Protocol):
    def search(self, terms: Sequence[str], limit: int) -> tuple[Tool, ...]: ...

    def all(self) -> tuple[Tool, ...]: ...

    def refresh(self, server_id: str) -> None: ...
