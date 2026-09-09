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
PACK_DEFECTS = (KeyError, TypeError, ValueError, yaml.YAMLError)


def extract_type_const(attributes_yaml: str, pack_name: str) -> str:
    """The pack's canonical ``@type``, read from ``x-jsonld``.

    A pack declares its type twice. ``x-jsonld."@type"`` is a plain scalar;
    ``properties["@type"]`` is a ``oneOf`` because the schema also permits an
    array containing the canonical type alongside provider-defined ones.

    This reads ``x-jsonld``: one scalar with nothing to unwrap, and
    ``@context`` sits beside it so both come from the same place. Reading
    ``properties["@type"]["const"]`` raised ``KeyError('const')`` on every
    real pack — the ``const`` is nested inside the ``oneOf``.

    Public: ``entrypoint/composition.py`` reads the same block for the
    planner's schema dict and must fail the same way — one bad pack skipped,
    not the whole boot.
    """

    return _x_jsonld(attributes_yaml, pack_name)["@type"]


def _x_jsonld(attributes_yaml: str, pack_name: str) -> dict:
    parsed = yaml.safe_load(attributes_yaml)
    schema = parsed["components"]["schemas"][pack_name]
    x_jsonld = schema.get("x-jsonld")
    if not x_jsonld:
        raise ValueError(f"no x-jsonld block for pack {pack_name}")
    return x_jsonld


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
            type_const = extract_type_const(pack.attributes_yaml, pack.pack_name)
            # sorted: set iteration order varies with PYTHONHASHSEED, and this
            # order reaches the discover request's jsonpath filter.
            categories = sorted(_extract_subject_categories(pack.examples_json))
        except PACK_DEFECTS as exc:
            skipped.append(SchemaPackSkipped(pack.pack_name, repr(exc)))
            continue
        for category in categories:
            for action_type in _ACTION_TYPES:
                index[(category, action_type)].append(type_const)
    return {key: tuple(values) for key, values in index.items()}, tuple(skipped)


def build_schema_context_index(
    packs: tuple[SchemaPackFiles, ...],
) -> tuple[dict[str, str], tuple[SchemaPackSkipped, ...]]:
    """Maps each @type to the ``@context`` URL its pack declares.

    The URL comes from the pack's own ``x-jsonld."@context"``, not from
    concatenating a base URL with the pack name and version. Both reach the
    network — discover sends it in ``schemaContext``, select in
    ``resourceAttributes`` — and the pack is what states it. Building the
    string happened to match, but it was our guess at a value already
    published.

    Skips the same malformed packs as build_capability_index: if one index
    kept a pack the other dropped, discovery could resolve a @type that has
    no schemaContext URL.
    """
    index: dict[str, str] = {}
    skipped: list[SchemaPackSkipped] = []
    for pack in packs:
        try:
            x_jsonld = _x_jsonld(pack.attributes_yaml, pack.pack_name)
            index[x_jsonld["@type"]] = x_jsonld["@context"]
        except PACK_DEFECTS as exc:
            skipped.append(SchemaPackSkipped(pack.pack_name, repr(exc)))
            continue
    return index, tuple(skipped)
