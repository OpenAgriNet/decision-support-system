"""The in-process redactor: the configured rules, run in this process.

The work is CPU-bound, so it runs on a worker thread rather than holding the
event loop. A name-finding model, when one is configured, adds its candidates
here.
"""

from __future__ import annotations

from collections.abc import Sequence

import anyio.to_thread

from dss.core.redaction.models import RedactionConfig
from dss.core.redaction.service import Redaction, redact


class LocalRedactor:
    def __init__(self, config: RedactionConfig) -> None:
        self._config = config

    async def redact(self, texts: Sequence[str]) -> Redaction:
        return await anyio.to_thread.run_sync(redact, list(texts), self._config)
