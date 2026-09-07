"""Skill discovery behaviour (design v2 §6.3).

PLACEHOLDER. Real skill routing — filtering the deployment's local skill set by
the turn's asks, honouring the ``/config/skills.yaml`` disable list — is not built
yet. Until it is, discovery selects nothing: an empty ``Skills`` is a valid,
non-blocking result, so the planner simply runs without extra guidance.

Local only: it must never make a network call (the barrier — design v2 §3).
"""

from __future__ import annotations

from dss.core.intent.models import Intent
from dss.core.shared.models import UserTurn
from dss.core.skills.models import Skills


async def discover_skills(turn: UserTurn, intent: Intent) -> Skills:
    # No routing yet — see module docstring.
    return Skills(selected=())
