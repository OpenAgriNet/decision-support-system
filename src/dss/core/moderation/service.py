"""The moderation evaluator (spec 0004).

Order is deliberate: deterministic policies run first in Python — free and exact —
then a single batched LLM call judges the remaining policies over the (possibly
sanitized) query. A query already cleaned or rejected deterministically never
costs a model call it doesn't need.

Flow for this slice:

    enriched_query
      → word-check policies      strip listed words, collect warnings (non-terminal)
      → llm policies (one call)  reject if the model reports a violation
      → PROCEED                  carrying the sanitized query + warnings

Harm wins over sanitization: a rejected turn carries no sanitized query, enforced
structurally by ``ModerationDecision`` rather than by remembering evaluation order.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from dss.core.moderation.models import (
    LlmModerationVerdict,
    ModerationContext,
    ModerationDecision,
    Outcome,
    ReasonCode,
)
from dss.core.policy.models import (
    FailMode,
    LlmPolicy,
    Policy,
    WordCheckPolicy,
)
from dss.ports.llm import LLMProvider

# DSS policy ids map to a closed reason code. An id absent here is treated as an
# adopter policy (``ADOPTER_POLICY`` + its namespaced id). "delete the prompt/code"
# is an instruction-manipulation attack, which is what ROLE_OBFUSCATION covers.
_DSS_REASON_CODES: dict[str, ReasonCode] = {
    "delete-command": ReasonCode.ROLE_OBFUSCATION,
}


def _reason_for(policy_id: str) -> ReasonCode:
    return _DSS_REASON_CODES.get(policy_id, ReasonCode.ADOPTER_POLICY)


def _strip_words(query: str, words: Sequence[str]) -> tuple[str, bool]:
    """Remove whole-word, case-insensitive matches of ``words`` and normalise the
    whitespace left behind. Returns the cleaned query and whether anything changed.

    Whole-word so "shitake" is left alone; the substring trap is a classic
    profanity-filter bug (the "Scunthorpe problem")."""

    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b",
        re.IGNORECASE,
    )
    stripped = pattern.sub("", query)
    if stripped == query:
        return query, False
    # Collapse the double spaces / stray spaces-before-punctuation removal leaves.
    stripped = re.sub(r"\s+([.,!?;:])", r"\1", stripped)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip()
    return stripped, True


def build_llm_prompt(policies: Sequence[LlmPolicy]) -> str:
    """Render the LLM policies' signals and examples into one judgment prompt.

    A plain string — building it names no framework, so it stays in core. The
    adapter turns the returned ``LlmModerationVerdict`` schema into structured
    output."""

    lines = [
        "You are a moderation classifier for an agriculture assistant.",
        "Decide whether the user's query violates any of the policies below.",
        "A query may be a genuine question even if blunt; only flag a real violation.",
        "",
        "Policies:",
    ]
    for policy in policies:
        lines.append(f"- id: {policy.id}")
        lines.append(f"  what it catches: {policy.description.strip()}")
        for signal in policy.signals:
            lines.append(f"    * {signal}")
        for example in policy.examples:
            lines.append(f"    e.g. {example.query!r} -> {example.expect.value}")
    lines += [
        "",
        "Return the id of the single violated policy, or null if none apply.",
    ]
    return "\n".join(lines)


async def moderate(
    context: ModerationContext,
    policies: Sequence[Policy],
    llm: LLMProvider,
) -> ModerationDecision:
    """Evaluate the moderation policies against the turn and return a verdict."""

    query = context.turn.enriched_query
    warnings: list[str] = []
    sanitized_query: str | None = None

    # 1. Deterministic policies — sanitize in place, in declared order.
    for policy in policies:
        if isinstance(policy, WordCheckPolicy):
            cleaned, changed = _strip_words(query, policy.words)
            if changed:
                query = cleaned
                sanitized_query = cleaned
                warnings.append(policy.warning)

    # 2. LLM policies — one batched call over the (possibly sanitized) query.
    llm_policies = [p for p in policies if isinstance(p, LlmPolicy)]
    if llm_policies:
        try:
            verdict = await llm.structured(
                system_prompt=build_llm_prompt(llm_policies),
                user_query=query,
                schema=LlmModerationVerdict,
            )
        except Exception:
            return _on_llm_failure(llm_policies)

        if verdict.violated_policy_id:
            fired = next(
                (p for p in llm_policies if p.id == verdict.violated_policy_id),
                None,
            )
            if fired is not None:
                return ModerationDecision(
                    outcome=fired.on_violation,
                    reason_code=_reason_for(fired.id),
                    violated_policy_id=fired.id,
                )

    # 3. Nothing rejected — proceed, carrying any sanitization forward.
    return ModerationDecision(
        outcome=Outcome.PROCEED,
        sanitized_query=sanitized_query,
        warnings=warnings,
    )


def _on_llm_failure(llm_policies: Sequence[LlmPolicy]) -> ModerationDecision:
    """Retries are the adapter's job; by here they are exhausted. Fail closed
    unless *every* LLM policy in play opted into ``fail_mode: open`` — one
    safety-critical closed policy is enough to reject the turn."""

    if all(p.fail_mode is FailMode.OPEN for p in llm_policies):
        return ModerationDecision(outcome=Outcome.PROCEED)
    return ModerationDecision(
        outcome=Outcome.REJECT,
        reason_code=ReasonCode.MODERATION_UNAVAILABLE,
    )
