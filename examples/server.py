"""Throwaway dev HTTP harness for moderation — NOT the DSS entrypoint.

The committed entrypoint (REST/gRPC/in-process) is undecided and needs an ADR
(DSS_ARCHITECTURE.md §8.3). This exists only so the two shipped policies can be
exercised with curl and traced in Langfuse during development.

Observability: Pydantic AI is instrumented via Logfire, exporting OpenTelemetry
spans to whatever ``OTEL_EXPORTER_OTLP_ENDPOINT`` points at (a self-hosted
Langfuse here). The deterministic ``profanity-filter`` makes no LLM call, so a
manual span around ``moderate()`` ensures *every* turn appears in Langfuse, not
just the ``delete-command`` LLM path.

Run:
    uv run uvicorn examples.server:app --port 8000
"""

from __future__ import annotations

import logfire
from fastapi import FastAPI
from pydantic import BaseModel

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.config.policy_loader import load_policy_pack
from dss.config.settings import Settings
from dss.core.moderation.messages import messages_for
from dss.core.moderation.models import ModerationContext
from dss.core.moderation.service import moderate
from dss.core.policy.models import Checkpoint
from dss.core.shared.models import UserTurn

# send_to_logfire=False → export only via OTEL_* env vars (Langfuse). With no env
# set, spans are simply created and dropped, so this stays silent offline.
logfire.configure(service_name="dss-moderation", send_to_logfire=False)
logfire.instrument_pydantic_ai()

_settings = Settings()
_policies = load_policy_pack(_settings.policy_config_path).for_checkpoint(
    Checkpoint.MODERATION
)
_llm = PydanticAILLMProvider(
    _settings.moderation_model,
    temperature=_settings.moderation_temperature,
    timeout=_settings.moderation_timeout_seconds,
    retries=_settings.moderation_retries,
)

app = FastAPI(title="DSS moderation dev harness")


class ModerateRequest(BaseModel):
    query: str
    session_id: str = "curl"
    source_lang: str = "en"
    target_lang: str = "en"
    channel: str = "web"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "policies": ",".join(p.id for p in _policies)}


@app.post("/moderate")
async def moderate_endpoint(req: ModerateRequest) -> dict:
    with logfire.span("dss.moderation", query=req.query):
        ctx = ModerationContext(
            turn=UserTurn(
                original_query=req.query,
                enriched_query=req.query,
                session_id=req.session_id,
                source_lang=req.source_lang,
                target_lang=req.target_lang,
                channel=req.channel,
            )
        )
        decision = await moderate(ctx, _policies, _llm)
        payload = {
            "outcome": decision.outcome.value,
            "reason_code": (
                decision.reason_code.value if decision.reason_code else None
            ),
            "violated_policy_id": decision.violated_policy_id,
            "sanitized_query": decision.sanitized_query,
            "warnings": decision.warnings,
            "messages": messages_for(decision),
        }
        logfire.info("moderation decision", **payload)
        return payload
