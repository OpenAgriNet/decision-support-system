"""Builds the capability index from schema pack files.

`profile.json` says which rule indexes a pack. A catalog pack is indexed under
the subjectCategories its examples carry, for both Knowledge and Service asks,
until the network declares a per-pack action type. An API pack's examples carry
no subjectCategories, so it is indexed under the name its @type ends in, for the
asks its `interaction_type` serves.
"""

from __future__ import annotations

import itertools
import json
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass

from dss.core.schema_packs.models import SchemaPackFiles, SchemaPackSkipped
from dss.core.schema_packs.service import (
    PACK_DEFECTS,
    extract_schema_context,
    extract_type_const,
    is_api_pack,
    pack_needs_place,
)

_ACTION_TYPES = ("Knowledge", "Service")
# Asking how to file a grievance is advice; filing one is a service.
_ACTION_TYPES_BY_PACK_INTERACTION: Mapping[str, tuple[str, ...]] = {
    "Advise": ("Knowledge",),
    "Observe": ("Service",),
    "Transact": ("Service",),
}


def build_place_bound_types(packs: tuple[SchemaPackFiles, ...]) -> frozenset[str]:
    return frozenset(
        extract_type_const(pack.attributes_yaml, pack.pack_name)
        for pack in packs
        if pack_needs_place(pack)
    )


@dataclass(frozen=True)
class _IndexEntry:
    type_const: str
    categories: tuple[str, ...]
    action_types: tuple[str, ...]

    def index_keys(self) -> Iterator[tuple[str, str]]:
        return itertools.product(self.categories, self.action_types)


def _read_index_entry_or_skip(pack: SchemaPackFiles) -> _IndexEntry | SchemaPackSkipped:
    try:
        return _read_index_entry(pack)
    except PACK_DEFECTS as exc:
        return SchemaPackSkipped(pack.pack_name, repr(exc))


def _read_index_entry(pack: SchemaPackFiles) -> _IndexEntry:
    type_const = extract_type_const(pack.attributes_yaml, pack.pack_name)
    if is_api_pack(pack):
        return _read_api_entry(pack, type_const)
    return _read_catalog_entry(pack, type_const)


def _read_catalog_entry(pack: SchemaPackFiles, type_const: str) -> _IndexEntry:
    # sorted: set iteration order varies with PYTHONHASHSEED, and this
    # order reaches the discover request's jsonpath filter.
    categories = sorted(_extract_subject_categories(pack.examples_json))
    return _IndexEntry(
        type_const=type_const,
        categories=tuple(categories),
        action_types=_ACTION_TYPES,
    )


def _read_api_entry(pack: SchemaPackFiles, type_const: str) -> _IndexEntry:
    interaction = json.loads(pack.profile_json).get("interaction_type")
    action_types = _ACTION_TYPES_BY_PACK_INTERACTION.get(interaction)
    if action_types is None:
        raise ValueError(
            f"pack {pack.pack_name} has no known interaction_type: {interaction!r}"
        )
    return _IndexEntry(
        type_const=type_const,
        categories=(_category_from_type(type_const, pack.pack_name),),
        action_types=action_types,
    )


def _category_from_type(type_const: object, pack_name: str) -> str:
    """``openagrinet:Grievance`` → ``Grievance``.

    A stopgap until network-specs says how an API pack declares its categories.
    """
    if not isinstance(type_const, str):
        raise TypeError(f"pack {pack_name} has a non-string @type: {type_const!r}")
    _prefix, _, type_name = type_const.rpartition(":")
    if not type_name:
        raise ValueError(f"pack {pack_name} @type names no type: {type_const!r}")
    return type_name


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
    outcomes = [_read_index_entry_or_skip(pack) for pack in packs]
    entries = [o for o in outcomes if isinstance(o, _IndexEntry)]
    skipped = tuple(o for o in outcomes if isinstance(o, SchemaPackSkipped))
    return _index_entries(entries), skipped


def _index_entries(
    entries: Iterable[_IndexEntry],
) -> dict[tuple[str, str], tuple[str, ...]]:
    # Types under one key stay in pack order; that order reaches discover.
    index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in entries:
        for key in entry.index_keys():
            index[key].append(entry.type_const)
    return {key: tuple(types) for key, types in index.items()}


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
            type_const = extract_type_const(pack.attributes_yaml, pack.pack_name)
            index[type_const] = extract_schema_context(
                pack.attributes_yaml, pack.pack_name
            )
        except PACK_DEFECTS as exc:
            skipped.append(SchemaPackSkipped(pack.pack_name, repr(exc)))
            continue
    return index, tuple(skipped)
