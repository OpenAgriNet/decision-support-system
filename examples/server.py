"""Throwaway dev HTTP harness for a turn — NOT the DSS entrypoint.

The committed entrypoint (REST/gRPC/in-process) is undecided and needs an ADR
(DSS_ARCHITECTURE.md §8.3). This exists only so the shipped intent + moderation
flow can be exercised with curl and traced in Langfuse during development.

It accepts the Experience-API envelope (ADR-0004), normalizes it to a
``UserTurn``, and runs intent classification and moderation in parallel
(ADR-0003), each bound to its own model from settings.

Observability: Pydantic AI is instrumented via Logfire, exporting OpenTelemetry
spans to whatever ``OTEL_EXPORTER_OTLP_ENDPOINT`` points at (a self-hosted
Langfuse here). A manual span around ``run_turn()`` ensures *every* turn appears,
including the deterministic (no-LLM) moderation path.

Run:
    uv run uvicorn examples.server:app --port 8000
"""

from __future__ import annotations

from datetime import UTC, datetime

import logfire
from fastapi import FastAPI

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.config.policy_loader import load_policy_pack
from dss.config.settings import Settings
from dss.core.moderation.messages import messages_for
from dss.core.policy.models import Checkpoint
from dss.orchestration.envelope import TurnEnvelope, to_user_turn
from dss.orchestration.turn import run_turn

# send_to_logfire=False → export only via OTEL_* env vars (Langfuse). With no env
# set, spans are simply created and dropped, so this stays silent offline.
# metrics=False: Langfuse's OTEL endpoint ingests traces, not metrics, so leaving
# metrics on produces a constant "Failed to export metrics batch" line.
logfire.configure(service_name="dss-turn", send_to_logfire=False, metrics=False)
logfire.instrument_pydantic_ai()

_settings = Settings()
_policies = load_policy_pack(_settings.policy_config_path).for_checkpoint(
    Checkpoint.MODERATION
)
# One provider per component — each may point at a different model (ADR-0004).
_intent_llm = PydanticAILLMProvider(
    _settings.intent_model,
    temperature=_settings.intent_temperature,
    timeout=_settings.intent_timeout_seconds,
    retries=_settings.intent_retries,
)
_moderation_llm = PydanticAILLMProvider(
    _settings.moderation_model,
    temperature=_settings.moderation_temperature,
    timeout=_settings.moderation_timeout_seconds,
    retries=_settings.moderation_retries,
)

app = FastAPI(title="DSS turn dev harness")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policies": ",".join(p.id for p in _policies)}


@app.post("/turn")
async def turn_endpoint(envelope: TurnEnvelope) -> dict:
    turn = to_user_turn(envelope, now=datetime.now(UTC))
    with logfire.span("dss.turn", query=turn.original_query):
        result = await run_turn(
            turn,
            intent_llm=_intent_llm,
            moderation_llm=_moderation_llm,
            policies=_policies,
        )
        decision = result.decision
        payload = {
            "intent": result.intent.model_dump(mode="json"),
            "decision": {
                "outcome": decision.outcome.value,
                "reason_code": (
                    decision.reason_code.value if decision.reason_code else None
                ),
                "violated_policy_id": decision.violated_policy_id,
                "sanitized_query": decision.sanitized_query,
                "warnings": decision.warnings,
                "frustration_detected": decision.frustration_detected,
            },
            "messages": messages_for(decision),
        }
        logfire.info("turn result", **payload)
        return payload
