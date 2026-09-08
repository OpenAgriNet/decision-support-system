"""Throwaway dev HTTP harness for the orchestrator — NOT the DSS entrypoint.

The committed entrypoint (REST/gRPC/in-process) is undecided and needs an ADR
(DSS_ARCHITECTURE.md §8.3). This exists only so the orchestrator workflow
(``orchestration/orchestrator.py::run_turn``, ADR-0006) can be exercised with curl.

Intent and moderation are the real services, run against an Azure OpenAI deployment
(its v1 Responses endpoint). Provider discovery genuinely needs the network adapter,
so it is stubbed here with ``_demo_discover`` so a turn reaches the planner offline.
The planner itself is a placeholder that owns plan creation; response composition and
channel shaping are separate components, not yet built, so the harness returns the
turn's ``TurnResult`` (outcome, intent, plan) as JSON.

Prerequisites (in the environment / .env):
    AZURE_OPENAI_ENDPOINT     e.g. https://<res>.services.ai.azure.com/openai/v1/responses
    AZURE_OPENAI_API_KEY      the deployment key
    AZURE_OPENAI_DEPLOYMENT   the Azure deployment id (no spaces)

Run:
    uv run uvicorn examples.orchestrate:app --port 8000
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import FastAPI

from dss.adapters.llm.pydantic_ai_provider import build_azure_llm
from dss.config.policy_loader import load_policy_pack
from dss.config.settings import Settings
from dss.core.intent.models import Intent
from dss.core.policy.models import Checkpoint
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability
from dss.core.shared.models import UserTurn
from dss.orchestration.envelope import TurnEnvelope, to_user_turn
from dss.orchestration.orchestrator import build_components, run_turn

_settings = Settings()
_policies = load_policy_pack(_settings.policy_config_path).for_checkpoint(
    Checkpoint.MODERATION
)

# Azure OpenAI backs both components. Fail fast if the creds are missing.
_AZURE_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
_AZURE_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
_AZURE_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
if not (_AZURE_ENDPOINT and _AZURE_KEY and _AZURE_DEPLOYMENT):
    raise RuntimeError(
        "Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and "
        "AZURE_OPENAI_DEPLOYMENT in the environment (.env) before starting."
    )


def _make_llm(temperature: float, timeout: float, retries: int):
    return build_azure_llm(
        _AZURE_DEPLOYMENT,
        endpoint=_AZURE_ENDPOINT,
        api_key=_AZURE_KEY,
        temperature=temperature,
        timeout=timeout,
        retries=retries,
    )


_intent_llm = _make_llm(
    _settings.intent_temperature,
    _settings.intent_timeout_seconds,
    _settings.intent_retries,
)
_moderation_llm = _make_llm(
    _settings.moderation_temperature,
    _settings.moderation_timeout_seconds,
    _settings.moderation_retries,
)


async def _demo_discover(
    intent: Intent, turn: UserTurn, now: datetime
) -> DiscoveryResult:
    """STUB for Provider Discovery. Returns one OnDemand capability per ask so the
    planner has something to plan. Swap for the real network adapter
    (``build_discover_providers``) to make discovery live."""

    capabilities = {
        index: (
            ProviderCapability(
                provider_id="demo-provider",
                provider_name="Demo Provider",
                capability=f"openagrinet:{ask.subject_categories.value}Capability",
                resource_id=f"res:demo:{index}",
            ),
        )
        for index, ask in enumerate(intent.asks)
    }
    return DiscoveryResult(
        answers={}, capabilities=capabilities, failures={}, events=()
    )


_components = build_components(
    intent_llm=_intent_llm,
    moderation_llm=_moderation_llm,
    policies=_policies,
    discover_providers=_demo_discover,
)

app = FastAPI(title="DSS orchestrator dev harness")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policies": ",".join(p.id for p in _policies)}


@app.post("/turn")
async def turn_endpoint(envelope: TurnEnvelope) -> dict:
    turn = to_user_turn(envelope, now=datetime.now(UTC))
    result = await run_turn(turn, _components, now=datetime.now(UTC))
    return result.model_dump(mode="json")
