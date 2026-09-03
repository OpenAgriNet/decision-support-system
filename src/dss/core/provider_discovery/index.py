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

from dss.core.provider_discovery.models import SchemaPackFiles, SchemaPackSkipped

_ACTION_TYPES = ("Knowledge", "Service")

# The pack is read as data, so any shape defect surfaces as one of these.
_PACK_DEFECTS = (KeyError, TypeError, ValueError, yaml.YAMLError)


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
) -> tuple[dict[tuple[str, str], tuple[str, ...]], tuple[SchemaPackSkipped, ...]]:
    """Returns the index and the packs left out of it.

    A malformed pack is skipped rather than raised, so one bad pack in the
    external network-specs checkout can't blind every other capability. The
    skips are returned, not swallowed — see SchemaPackSkipped.
    """
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    skipped: list[SchemaPackSkipped] = []
    for pack in packs:
        try:
            type_const = _extract_type_const(pack.attributes_yaml, pack.pack_name)
            # sorted: set iteration order varies with PYTHONHASHSEED, and this
            # order reaches the discover request's jsonpath filter.
            categories = sorted(_extract_subject_categories(pack.examples_json))
        except _PACK_DEFECTS as exc:
            skipped.append(SchemaPackSkipped(pack.pack_name, repr(exc)))
            continue
        for category in categories:
            for action_type in _ACTION_TYPES:
                index[(category, action_type)].append(type_const)
    return {key: tuple(values) for key, values in index.items()}, tuple(skipped)


def build_schema_context_index(
    packs: tuple[SchemaPackFiles, ...],
) -> tuple[dict[str, tuple[str, str]], tuple[SchemaPackSkipped, ...]]:
    """Maps each @type to the (pack_name, version) that declares it.

    Used to build the discover request's schemaContext URLs, which need a
    pack's name and version — the @type string alone doesn't carry either.

    Skips the same malformed packs as build_capability_index: if one index
    kept a pack the other dropped, discovery could resolve a @type that has
    no schemaContext URL.
    """
    index: dict[str, tuple[str, str]] = {}
    skipped: list[SchemaPackSkipped] = []
    for pack in packs:
        try:
            type_const = _extract_type_const(pack.attributes_yaml, pack.pack_name)
        except _PACK_DEFECTS as exc:
            skipped.append(SchemaPackSkipped(pack.pack_name, repr(exc)))
            continue
        index[type_const] = (pack.pack_name, pack.version)
    return index, tuple(skipped)
