"""Intent recognition — converts a query into a structured capability need.

Emits, per question in the turn, a category, the farmer's subject word, and an
action type. Classification lives here, not in moderation: moderation consumes
the intent object and judges harm only.
"""

from dss.core.intent.models import (
    BASE_TAXONOMY,
    ActionType,
    Ask,
    Intent,
    Taxonomy,
)
from dss.core.intent.service import classify

__all__ = [
    "BASE_TAXONOMY",
    "ActionType",
    "Ask",
    "Intent",
    "Taxonomy",
    "classify",
]
