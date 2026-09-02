"""Builds the capability index from schema pack files.

Only two facts are pulled from each pack: its @type const, and the
subjectCategories observed in its examples. action_type is not yet a real
signal in the packs, so every capability maps under both Knowledge and
Service until the network declares a per-pack horizontal category.
"""

from __future__ import annotations

import json
from collections import defaultdict

import yaml

from dss.core.provider_discovery.models import SchemaPackFiles

_ACTION_TYPES = ("Knowledge", "Service")


def _extract_type_const(attributes_yaml: str, pack_name: str) -> str:
    parsed = yaml.safe_load(attributes_yaml)
    schema = parsed["components"]["schemas"][pack_name]
    for member in schema["allOf"]:
        type_prop = member.get("properties", {}).get("@type")
        if type_prop is not None:
            return type_prop["const"]
    raise ValueError(f"no @type const found for pack {pack_name}")


def _extract_subject_categories(examples_json: tuple[str, ...]) -> set[str]:
    categories: set[str] = set()
    for example in examples_json:
        categories.update(json.loads(example)["subjectCategories"])
    return categories


def build_capability_index(
    packs: tuple[SchemaPackFiles, ...],
) -> dict[tuple[str, str], tuple[str, ...]]:
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pack in packs:
        type_const = _extract_type_const(pack.attributes_yaml, pack.pack_name)
        for category in _extract_subject_categories(pack.examples_json):
            for action_type in _ACTION_TYPES:
                index[(category, action_type)].append(type_const)
    return {key: tuple(values) for key, values in index.items()}
