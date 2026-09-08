"""The default telemetry sink — one JSON line per stage.

Default on purpose: a deployment that binds no tracing backend still produces
evidence. An adopter swaps this for their own without a DSS change.

It decides nothing about what is worth recording. `outcome` is a short string
the caller already computed, never the query — tracing backends capture prompt
text by default, which is exactly what must not happen here.
"""

from __future__ import annotations

import json

from dss.core.shared.models import TurnContext


class StdoutTelemetrySink:
    def stage(self, name: str, ctx: TurnContext, outcome: str) -> None:
        print(
            json.dumps(
                {"trace_id": ctx.trace_id, "stage": name, "outcome": outcome},
                ensure_ascii=False,
            )
        )
