"""The composition root — the one place that names every concrete type.

Swapping a stub for a real implementation is a change here and nowhere else.
Nothing outside this module imports a concrete adapter.

One provider per component, because each may point at a different model
(ADR-0004). Policies are loaded for the moderation checkpoint only; other
checkpoints are declared but not yet evaluated.
"""

from __future__ import annotations

import warnings
from datetime import datetime

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.adapters.llm.stub import StubLLM
from dss.adapters.sinks.file import FileTelemetrySink, FileTurnSink
from dss.config.policy_loader import load_policy_pack
from dss.config.settings import Settings
from dss.core.intent.models import Intent
from dss.core.policy.models import Checkpoint
from dss.core.provider_discovery.models import DiscoveryResult
from dss.core.shared.models import UserTurn
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

    policies = load_policy_pack(settings.policy_config_path).for_checkpoint(
        Checkpoint.MODERATION
    )
    return CoreRunner(
        intent_llm=_intent_llm(settings),
        moderation_llm=_moderation_llm(settings),
        policies=policies,
        discover_providers=_discovers_nothing,
        turns=FileTurnSink(settings.turns_path),
        telemetry=FileTelemetrySink(settings.telemetry_path),
    )


async def _discovers_nothing(
    intent: Intent, turn: UserTurn, *, now: datetime
) -> DiscoveryResult:
    """Provider discovery is NOT wired — every turn finds nobody to call.

    `CoreRunner` requires a `DiscoverProviders` so that nothing can be built
    without deciding what discovery to give it. Building the real one needs a
    schema-pack checkout on disk, an HTTP client, and six settings that do not
    exist yet (base URLs, coverage radius, sender/receiver ids). Tracked in
    TODO.md under "Planner Agent (#10)".

    Replacing this one function is the whole change — the seam is already
    typed and in place.
    """

    return DiscoveryResult(answers={}, capabilities={}, failures={}, events=())


def _intent_llm(settings: Settings):
    if settings.stub_llm:
        return StubLLM()  # STUB(#83): canned answers, no network
    return PydanticAILLMProvider(
        settings.intent_model,
        temperature=settings.intent_temperature,
        timeout=settings.intent_timeout_seconds,
        retries=settings.intent_retries,
    )


def _moderation_llm(settings: Settings):
    if settings.stub_llm:
        return StubLLM()
    return PydanticAILLMProvider(
        settings.moderation_model,
        temperature=settings.moderation_temperature,
        timeout=settings.moderation_timeout_seconds,
        retries=settings.moderation_retries,
    )
