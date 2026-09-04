"""The composition root — the one place that names every concrete type.

Swapping the stub core for the real one is a change to this file and nowhere
else. Nothing outside it imports a concrete adapter.
"""

from __future__ import annotations

import warnings

from dss.adapters.llm.stub import StubLLM
from dss.adapters.sinks.file import FileTelemetrySink, FileTurnSink
from dss.entrypoint.settings import Settings
from dss.orchestration.core_runner import CoreRunner
from dss.ports.turn import TurnRunner

UNWIRED_URL = (
    "DSS_EVIDENCE_URL is set but posting evidence to an external endpoint is not "
    "implemented — records are being written to {directory} instead. Two things "
    "have to be settled first: the HTTP client dependency, and what an "
    "unreachable endpoint should do to a turn (the turn sink is required, so a "
    "failed write currently fails the turn)."
)


def build_runner(settings: Settings) -> TurnRunner:
    if settings.evidence_url:
        warnings.warn(
            UNWIRED_URL.format(directory=settings.evidence_dir),
            UserWarning,
            stacklevel=2,
        )
    return CoreRunner(
        llm=StubLLM(),  # STUB(#83): canned answers; swap for a real provider here
        turns=FileTurnSink(settings.turns_path),
        telemetry=FileTelemetrySink(settings.telemetry_path),
    )
