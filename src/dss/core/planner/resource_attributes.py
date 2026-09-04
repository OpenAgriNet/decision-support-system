"""Assembles resourceAttributes for a /select call (plan issue #10).

Structural fields (@context, @type, subjectCategories, location) come from
discovery data and the turn — never from the model. The model's own
resource_attributes (a resolved commodity code, topics, ...) merge on top,
but cannot override a structural field: the model chooses the capability by
resource_id, not by rewriting @type after the fact.
"""

from __future__ import annotations

from dss.core.provider_discovery.models import ProviderCapability
from dss.core.shared.models import UserTurn


def _location_field(turn: UserTurn) -> dict | None:
    if turn.location is None or turn.location.geometry is None:
        return None
    return {
        "type": turn.location.geometry.type,
        "coordinates": turn.location.geometry.coordinates,
    }


def build_resource_attributes(
    *,
    capability: ProviderCapability,
    turn: UserTurn,
    model_filled: dict,
    schema_context_index: dict[str, tuple[str, str]],
    schema_base_url: str,
) -> dict:
    """Build the full resourceAttributes object: structural fields first, the
    model's fields merged on top — but structural fields always win."""

    pack_name, version = schema_context_index[capability.capability]
    structural: dict = {
        "@context": f"{schema_base_url}/{pack_name}/{version}/context.jsonld",
        "@type": capability.capability,
        "subjectCategories": list(capability.observed_categories),
    }

    location = _location_field(turn)
    if location is not None:
        structural["location"] = location

    return {**model_filled, **structural}
