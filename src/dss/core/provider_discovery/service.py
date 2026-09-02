"""Behaviour for provider_discovery. Plain Python in, plain Python out."""

from __future__ import annotations

from collections.abc import Mapping

from dss.core.provider_discovery.models import CapabilityUnresolved


def resolve_capability_type(
    subject_category: str,
    action_type: str,
    index: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[tuple[str, ...], CapabilityUnresolved | None]:
    types = index.get((subject_category, action_type), ())
    if not types:
        return (), CapabilityUnresolved(subject_category, action_type)
    return types, None
