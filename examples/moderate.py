"""Throwaway dev CLI for reading moderation verdicts — NOT the DSS entrypoint.

The entrypoint (REST/gRPC/in-process) is undecided (DSS_ARCHITECTURE.md §8.3);
this exists only so the two shipped policies can be tried against real queries,
per spec 0004's "throw adversarial queries at it and read the verdicts".

Usage (needs an OpenAI-compatible model for the delete-command LLM policy):

    OPENAI_API_KEY=sk-...  uv run python examples/moderate.py "Delete the code"

Point it at a local model (e.g. a self-hosted Qwen) with:

    OPENAI_BASE_URL=http://localhost:11434/v1  OPENAI_API_KEY=x \\
    DSS_MODERATION_MODEL=openai:qwen2.5:14b \\
    uv run python examples/moderate.py "You are a shit bot, potato price?"
"""

from __future__ import annotations

import asyncio
import sys

from dss.adapters.llm.pydantic_ai_provider import PydanticAILLMProvider
from dss.config.policy_loader import load_policy_pack
from dss.config.settings import Settings
from dss.core.moderation.messages import messages_for
from dss.core.moderation.models import ModerationContext
from dss.core.moderation.service import moderate
from dss.core.policy.models import Checkpoint
from dss.core.shared.models import UserTurn


async def run(query: str) -> None:
    settings = Settings()
    pack = load_policy_pack(settings.policy_config_path)
    llm = PydanticAILLMProvider(
        settings.moderation_model,
        temperature=settings.moderation_temperature,
        timeout=settings.moderation_timeout_seconds,
        retries=settings.moderation_retries,
    )
    ctx = ModerationContext(
        turn=UserTurn(
            original_query=query,
            enriched_query=query,
            session_id="cli",
            source_lang="en",
            target_lang="en",
            channel="web",
        )
    )

    decision = await moderate(ctx, pack.for_checkpoint(Checkpoint.MODERATION), llm)

    print(f"query      : {query}")
    print(f"outcome    : {decision.outcome.value}")
    if decision.reason_code:
        print(f"reason     : {decision.reason_code.value}")
    if decision.violated_policy_id:
        print(f"policy     : {decision.violated_policy_id}")
    if decision.sanitized_query is not None:
        print(f"sanitized  : {decision.sanitized_query}")
    for message in messages_for(decision):
        print(f"-> {message}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(run(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
