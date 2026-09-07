"""Throwaway dev HTTP harness for the *full* orchestrator — NOT the DSS entrypoint.

The committed entrypoint (REST/gRPC/in-process) is undecided and needs an ADR
(DSS_ARCHITECTURE.md §8.3). This exists only so the composed turn workflow
(``orchestration/orchestrator.py::run_turn``, ADR-0006) can be exercised with
curl end to end.

Intent and moderation are the *real* services, run against an Azure OpenAI
deployment (its v1 Responses endpoint). Everything the team has not built yet runs
as its shipped placeholder. The one component that genuinely needs an external
service — Provider Discovery, which hops to the network adapter — is stubbed with
``_demo_discover`` so the pipeline streams without it.

Prerequisites (in the environment / .env):
    AZURE_OPENAI_ENDPOINT     e.g. https://<res>.services.ai.azure.com/openai/v1/responses
    AZURE_OPENAI_API_KEY      the deployment key
    AZURE_OPENAI_DEPLOYMENT   the Azure deployment id (no spaces)

Run:
    uv run uvicorn examples.orchestrate:app --port 8000
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import FastAPI

from dss.adapters.llm.pydantic_ai_provider import build_azure_llm
from dss.config.policy_loader import load_policy_pack
from dss.config.settings import Settings
from dss.core.composition.models import Identity
from dss.core.intent.models import Intent
from dss.core.policy.models import Checkpoint
from dss.core.provider_discovery.models import DiscoveryResult, ProviderCapability
from dss.core.shared.models import UserTurn
from dss.core.tool_discovery.models import Tool
from dss.orchestration.envelope import TurnEnvelope, to_user_turn
from dss.orchestration.orchestrator import build_components, run_turn

_settings = Settings()
_policies = load_policy_pack(_settings.policy_config_path).for_checkpoint(
    Checkpoint.MODERATION
)

# Azure OpenAI backs both components. Both share one model here (the design lets a
# deployment bind each its own). Fail fast at startup if the creds are missing —
# there is no fallback backend.
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

# The tenant's assistant identity (the Identity config primitive). Hard-coded here;
# a real deployment loads it from /config.
_IDENTITY = Identity(
    name="Kisan Mitra",
    persona="A concise, friendly agriculture assistant.",
    boundaries="Answers agriculture questions only.",
)


async def _demo_discover(
    intent: Intent, turn: UserTurn, now: datetime
) -> DiscoveryResult:
    """STUB for Provider Discovery. Returns one OnDemand capability per ask so the
    placeholder planner/executioner/composer have something to run. Swap for the
    real network adapter (``build_discover_providers``) to make discovery live."""

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


class _EmptyToolIndex:
    """No MCP tools wired in this harness."""

    def all(self) -> tuple[Tool, ...]:
        return ()

    def search(self, terms: Sequence[str], limit: int) -> tuple[Tool, ...]:
        return ()

    def refresh(self, server_id: str) -> None:
        raise NotImplementedError


_components = build_components(
    intent_llm=_intent_llm,
    moderation_llm=_moderation_llm,
    policies=_policies,
    discover_providers=_demo_discover,
    tool_index=_EmptyToolIndex(),
    identity=_IDENTITY,
    enable_review=False,  # optional component left off (design v2 §6.9)
)

app = FastAPI(title="DSS orchestrator dev harness")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policies": ",".join(p.id for p in _policies)}


@app.post("/turn")
async def turn_endpoint(envelope: TurnEnvelope) -> dict:
    """Run a turn and collect the streamed chunks into one JSON body.

    The real transport is Server-Sent Events (design v2 §2) — one chunk per event
    on an open connection. This harness buffers them so the result is easy to read
    with a single curl."""

    turn = to_user_turn(envelope, now=datetime.now(UTC))
    chunks = [
        chunk.model_dump(mode="json")
        async for chunk in run_turn(turn, _components, now=datetime.now(UTC))
    ]
    return {"channel": turn.channel, "chunks": chunks}
