"""The capability tools port — the Agent Skills the planner may load.

A capability tool is a category's rules for asking a provider and reading
its reply (``capability_tools/en/market/SKILL.md``: which days to ask mandi
prices for, and why other markets' prices still answer). The planner is
offered those for the intent categories of the turn's asks, sees each one's
name and description, and loads one only when it needs it (ADR-0025).

The implementation (``config/capability_tools_service.py``) reads folders,
which is why this seam exists: ``core/`` and ``orchestration/`` may not know
where the files live.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dss.core.intent.models import SubjectCategory
from dss.core.shared.language import LanguageTag


@dataclass(frozen=True)
class CapabilityTools:
    """The Agent Skills a turn may load: those in the ``library_path`` folder
    named for the intent categories its asks are about. The model reads one
    only when it loads it."""

    library_path: str
    names: tuple[str, ...]


@runtime_checkable
class CapabilityToolProvider(Protocol):
    def get_capability_tools(
        self, lang: LanguageTag, categories: Collection[SubjectCategory]
    ) -> CapabilityTools | None:
        """The planner's Agent Skills for those intent categories, in ``lang``
        or English when it has none. ``None`` when none of them has one."""
        ...
